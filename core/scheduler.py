"""Recurring background jobs (autonomy): JSON-backed schedule + clock-injected loop.

State:  <ULTRON_HOME>/schedules.json   (hand-editable; missing/corrupt -> empty, never crash)

Scheduler is pure composition: the runner that executes a job's prompt is an
injected Callable, the clock is an injectable Callable (tests pass a fake),
and persistence is a JobStore path. ULTRON_HOME resolution mirrors
providers/registry.py (duplicated here because core never imports providers).
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RESULT_LIMIT = 280  # last_result is a quick CLI glimpse, not a transcript


def ultron_home() -> Path:
    return Path(os.environ.get("ULTRON_HOME", os.path.join(os.path.expanduser("~"), ".ultron")))


def schedules_path() -> Path:
    return ultron_home() / "schedules.json"


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f"bad job name {name!r}: expected slug matching {NAME_RE.pattern}")
    return name


@dataclass
class Job:
    name: str
    prompt: str                      # what the agent should do each run
    every_s: float                   # minimum seconds between runs (>0)
    enabled: bool = True
    last_run: float | None = None
    next_run: float = 0.0            # epoch
    last_result: str = ""            # truncated agent answer for quick CLI display

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        if not isinstance(data, dict):
            raise TypeError("job entry must be a JSON object")
        every_s = float(data["every_s"])
        if not math.isfinite(every_s) or every_s <= 0:
            raise ValueError(f"job {data.get('name')!r} has non-positive every_s")
        last_run = data.get("last_run")
        return cls(
            name=_check_name(data["name"]),
            prompt=str(data["prompt"]),
            every_s=every_s,
            enabled=bool(data.get("enabled", True)),
            last_run=None if last_run is None else float(last_run),
            next_run=float(data.get("next_run", 0.0)),
            last_result=str(data.get("last_result", "")),
        )


class JobStore:
    """dict[str, Job] persisted as JSON at `path`; CRUD helpers save immediately."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Job]:
        """Missing file -> {}; corrupted JSON -> {} (never crash). Bad individual
        entries are skipped so one hand-edit typo cannot wipe the schedule."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # missing/unreadable, invalid JSON
            return {}
        if not isinstance(raw, dict):
            return {}
        jobs: dict[str, Job] = {}
        for entry in raw.values():
            try:
                job = Job.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                continue
            jobs[job.name] = job
        return jobs

    def save(self, jobs: dict[str, Job]) -> None:
        """Atomic-ish: write a temp sibling, then os.replace over the target."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps({name: job.to_dict() for name, job in sorted(jobs.items())},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # --- CRUD ----------------------------------------------------------------

    def get(self, name: str) -> Job | None:
        return self.load().get(name)

    def list(self) -> list[Job]:
        return sorted(self.load().values(), key=lambda job: job.name)

    def add(self, job: Job) -> Job:
        _check_name(job.name)
        jobs = self.load()
        if job.name in jobs:
            raise ValueError(f"job {job.name!r} already exists (use update)")
        jobs[job.name] = job
        self.save(jobs)
        return job

    def update(self, job: Job) -> Job:
        _check_name(job.name)
        jobs = self.load()
        if job.name not in jobs:
            raise ValueError(f"unknown job {job.name!r}")
        jobs[job.name] = job
        self.save(jobs)
        return job

    def remove(self, name: str) -> None:
        _check_name(name)
        jobs = self.load()
        if name not in jobs:
            raise ValueError(f"unknown job {name!r}")
        del jobs[name]
        self.save(jobs)


class Scheduler:
    """Due-job bookkeeping around a JobStore. `now` is injectable for tests."""

    def __init__(self, store: JobStore, now: Callable[[], float] | None = None) -> None:
        self._store = store
        self._now = now or time.time

    def add_job(self, name: str, prompt: str, every_s: float,
                start_at: float | None = None) -> Job:
        _check_name(name)
        if not isinstance(every_s, (int, float)) or isinstance(every_s, bool) \
                or not math.isfinite(every_s) or every_s <= 0:
            raise ValueError(f"every_s must be a finite number > 0, got {every_s!r}")
        next_run = float(self._now()) if start_at is None else float(start_at)
        return self._store.add(Job(name=name, prompt=prompt, every_s=float(every_s),
                                   next_run=next_run))

    def due(self) -> list[Job]:
        """Enabled jobs with now >= next_run, earliest first."""
        now = self._now()
        return self._due_from(self._store.load(), now)

    def run_due(self, runner: Callable[[Job], str]) -> list[tuple[Job, str | None]]:
        """Run every due job through `runner`; a failing job never kills the loop.

        Each run (success or captured exception) reschedules the job:
        last_run=now, next_run=now+every_s, last_result=answer[:280].
        Returns [(job, error|None)]; persists the batch with one save().
        """
        jobs = self._store.load()
        now = self._now()
        results: list[tuple[Job, str | None]] = []
        for job in self._due_from(jobs, now):
            error: str | None = None
            try:
                answer = runner(job)
            except Exception as exc:  # capture, reschedule, keep going
                answer = str(exc)
                error = str(exc)
            job.last_run = now
            job.next_run = now + job.every_s
            job.last_result = answer[:RESULT_LIMIT]
            results.append((job, error))
        self._store.save(jobs)
        return results

    @staticmethod
    def _due_from(jobs: dict[str, Job], now: float) -> list[Job]:
        return sorted(
            (job for job in jobs.values() if job.enabled and now >= job.next_run),
            key=lambda job: job.next_run,
        )
