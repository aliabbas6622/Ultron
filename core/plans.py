"""Long-term planning: persistent, resumable task plans.

State:  <ULTRON_HOME>/plans/<name>.json   (one hand-editable JSON file per plan)

Resumability semantics: a plan is resumable because every step may carry a
`checkpoint` dict (last cursor, file written, page fetched, ...) and
`Plan.next_step()` always returns where execution left off -- the first
in_progress step, else the first pending one. Loading a plan from disk after
a crash therefore resumes at the first in_progress or pending step, with
whatever checkpoint state the crashed run last persisted. Steps advance via
`PlanStore.set_step_status(..., now=...)`; `now` is a plain injectable float
so callers (and tests) stay offline-deterministic -- no hidden wall clock.

PlanStore is pure composition: stdlib JSON persistence only, no bricks.
ULTRON_HOME resolution mirrors providers/registry.py (duplicated here
because core never imports providers).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STEP_STATUSES = ("pending", "in_progress", "done", "failed")


def ultron_home() -> Path:
    return Path(os.environ.get("ULTRON_HOME", os.path.join(os.path.expanduser("~"), ".ultron")))


def plans_dir() -> Path:
    return ultron_home() / "plans"


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f"bad plan name {name!r}: expected slug matching {NAME_RE.pattern}")
    return name


@dataclass
class PlanStep:
    description: str
    status: str = "pending"            # one of STEP_STATUSES
    checkpoint: dict = field(default_factory=dict)  # resumable state (last cursor, file written)
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStep":
        if not isinstance(data, dict):
            raise TypeError("step entry must be a JSON object")
        status = str(data.get("status", "pending"))
        if status not in STEP_STATUSES:
            raise ValueError(f"unknown step status {status!r}; expected one of {STEP_STATUSES}")
        checkpoint = data.get("checkpoint") or {}
        if not isinstance(checkpoint, dict):
            raise ValueError("step checkpoint must be a JSON object")
        return cls(
            description=str(data["description"]),
            status=status,
            checkpoint=dict(checkpoint),
            updated_at=float(data.get("updated_at", 0.0)),
        )


@dataclass
class Plan:
    name: str                          # slug matching NAME_RE
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = 0.0

    @property
    def status(self) -> str:
        """Rollup over step statuses, in deterministic precedence order
        (failed beats done beats in_progress):

        - "failed"      any step failed and none is in_progress
        - "done"        every step is done (a plan with no steps is not_started)
        - "in_progress" any step is in_progress, or some done with pendings left
        - "not_started" everything pending
        """
        statuses = [step.status for step in self.steps]
        if "failed" in statuses and "in_progress" not in statuses:
            return "failed"
        if statuses and all(s == "done" for s in statuses):
            return "done"
        if "in_progress" in statuses or ("done" in statuses and "pending" in statuses):
            return "in_progress"
        return "not_started"

    def next_step(self) -> PlanStep | None:
        """Where execution left off: the first in_progress step, else the
        first pending one. None when nothing is runnable (all done/failed)."""
        for step in self.steps:
            if step.status == "in_progress":
                return step
        for step in self.steps:
            if step.status == "pending":
                return step
        return None

    def progress(self) -> tuple[int, int]:
        """(done count, total steps)."""
        done = sum(1 for step in self.steps if step.status == "done")
        return done, len(self.steps)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        if not isinstance(data, dict):
            raise TypeError("plan entry must be a JSON object")
        return cls(
            name=_check_name(data["name"]),
            goal=str(data["goal"]),
            steps=[PlanStep.from_dict(entry) for entry in data.get("steps") or []],
            created_at=float(data.get("created_at", 0.0)),
        )


class PlanStore:
    """One JSON file per plan under `dir`; CRUD helpers save immediately."""

    def __init__(self, dir: Path) -> None:
        self._dir = Path(dir)

    @property
    def dir(self) -> Path:
        return self._dir

    def _path_for(self, name: str) -> Path:
        return self._dir / f"{name}.json"

    def save(self, plan: Plan) -> None:
        """Create the dir if needed, validate the name, write atomically-ish:
        temp sibling + os.replace, so a crash mid-write never truncates a
        saved plan."""
        _check_name(plan.name)
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(plan.name)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def load(self, name: str) -> Plan:
        """Missing plan -> ValueError; unreadable/corrupt file -> ValueError."""
        _check_name(name)
        path = self._path_for(name)
        if not path.is_file():
            raise ValueError(f"unknown plan {name!r}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return Plan.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"plan {name!r} is corrupted: {exc}") from exc

    def list(self) -> list[Plan]:
        """All plans sorted by name. Corrupted or unreadable files are
        SKIPPED, not crashed: one hand-edit typo must never hide the
        healthy plans (explicit load() still surfaces the error)."""
        plans: list[Plan] = []
        if not self._dir.is_dir():
            return plans
        for path in sorted(self._dir.glob("*.json")):
            try:
                plans.append(Plan.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, KeyError, TypeError, ValueError):
                continue
        return sorted(plans, key=lambda plan: plan.name)

    def delete(self, name: str) -> None:
        _check_name(name)
        path = self._path_for(name)
        if not path.is_file():
            raise ValueError(f"unknown plan {name!r}")
        path.unlink()

    def set_step_status(self, plan_name: str, index: int, status: str,
                        checkpoint: dict | None = None, now: float = 0.0) -> Plan:
        """Advance one step: validate, mutate, persist, return the plan.

        `checkpoint=None` keeps the step's existing checkpoint; a dict
        replaces it. `now` stamps step.updated_at and is injectable so tests
        stay deterministic. ValueError on bad index or status (or missing
        plan, via load()).
        """
        _check_name(plan_name)
        if status not in STEP_STATUSES:
            raise ValueError(f"unknown step status {status!r}; expected one of {STEP_STATUSES}")
        plan = self.load(plan_name)
        if not isinstance(index, int) or isinstance(index, bool) \
                or not 0 <= index < len(plan.steps):
            raise ValueError(
                f"bad step index {index!r} for plan {plan_name!r} "
                f"with {len(plan.steps)} steps")
        step = plan.steps[index]
        step.status = status
        if checkpoint is not None:
            step.checkpoint = dict(checkpoint)
        step.updated_at = float(now)
        self.save(plan)
        return plan
