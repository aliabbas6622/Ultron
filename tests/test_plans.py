"""Plan tests: status precedence (failed > done > in_progress), next_step
ordering, progress counts, PlanStore persistence (round trip incl.
checkpoints, corrupted file skipped on list), validation errors, and the
crash-resume contract (load + next_step picks up where execution left off)."""

from __future__ import annotations

import json

import pytest

from core.plans import Plan, PlanStep, PlanStore, plans_dir, ultron_home


def step(status: str = "pending", checkpoint: dict | None = None,
         updated_at: float = 0.0) -> PlanStep:
    return PlanStep(description=f"do {status}", status=status,
                    checkpoint=checkpoint or {}, updated_at=updated_at)


def make_plan(*statuses: str) -> Plan:
    return Plan(name="p", goal="g", steps=[step(s) for s in statuses])


# --- status precedence ----------------------------------------------------------

def test_status_not_started_when_all_pending():
    assert make_plan("pending", "pending").status == "not_started"


def test_status_not_started_when_plan_has_no_steps():
    assert make_plan().status == "not_started"


def test_status_in_progress_when_a_step_is_running():
    assert make_plan("done", "in_progress", "pending").status == "in_progress"


def test_status_in_progress_when_some_done_with_pendings_left():
    assert make_plan("done", "done", "pending").status == "in_progress"


def test_status_done_when_all_done():
    assert make_plan("done", "done").status == "done"


def test_failed_beats_done():
    assert make_plan("done", "failed").status == "failed"


def test_failed_with_only_failures_and_pendings():
    assert make_plan("failed").status == "failed"
    assert make_plan("pending", "failed").status == "failed"


def test_in_progress_step_outranks_an_earlier_failure():
    # "failed" requires none in_progress: a live step means the plan is
    # still running, and only a fully stopped plan surfaces as failed.
    assert make_plan("failed", "in_progress").status == "in_progress"


# --- next_step -----------------------------------------------------------------

def test_next_step_returns_first_in_progress_even_if_pending_came_earlier():
    plan = make_plan("done", "pending", "in_progress", "pending")
    assert plan.next_step() is plan.steps[2]


def test_next_step_returns_first_pending_when_none_in_progress():
    plan = make_plan("done", "pending", "pending")
    assert plan.next_step() is plan.steps[1]


def test_next_step_none_when_exhausted_or_only_failed_left():
    assert make_plan("done", "done").next_step() is None
    assert make_plan("done", "failed").next_step() is None
    assert make_plan().next_step() is None


# --- progress ------------------------------------------------------------------

def test_progress_counts_done_over_total():
    assert make_plan("done", "pending", "done", "failed").progress() == (2, 4)
    assert make_plan("in_progress").progress() == (0, 1)
    assert make_plan().progress() == (0, 0)


# --- store: save / load / list -------------------------------------------------

def test_store_round_trip_including_checkpoints(tmp_path):
    store = PlanStore(tmp_path / "plans")
    plan = Plan(name="migrate-db", goal="move data to the new schema", created_at=100.0, steps=[
        PlanStep(description="dump tables", status="done",
                 checkpoint={"last_row": 512}, updated_at=110.0),
        PlanStep(description="restore", status="in_progress",
                 checkpoint={"table": "users", "cursor": 42}, updated_at=120.0),
        PlanStep(description="verify counts"),
    ])
    store.save(plan)

    loaded = PlanStore(tmp_path / "plans").load("migrate-db")  # fresh store, same files
    assert loaded == plan
    assert loaded.steps[1].checkpoint == {"table": "users", "cursor": 42}
    assert loaded.steps[1].updated_at == 120.0
    assert loaded.created_at == 100.0


def test_save_creates_missing_dir_and_leaves_no_temp_file(tmp_path):
    plans = tmp_path / "nested" / "plans"
    store = PlanStore(plans)
    store.save(Plan(name="alpha", goal="g"))
    store.save(Plan(name="alpha", goal="g2"))  # overwrite must work too
    assert (plans / "alpha.json").is_file()
    assert list(plans.iterdir()) == [plans / "alpha.json"]
    assert PlanStore(plans).load("alpha").goal == "g2"


def test_list_sorted_by_name(tmp_path):
    store = PlanStore(tmp_path / "plans")
    for name in ("zeta", "alpha", "mid-2", "mid-1"):
        store.save(Plan(name=name, goal="g"))
    assert [p.name for p in store.list()] == ["alpha", "mid-1", "mid-2", "zeta"]


def test_list_missing_dir_returns_empty(tmp_path):
    assert PlanStore(tmp_path / "nope").list() == []


def test_list_skips_corrupted_but_load_works_for_good_ones(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "good.json").write_text(
        json.dumps(Plan(name="good", goal="fine", steps=[PlanStep("only step")]).to_dict()),
        encoding="utf-8")
    (plans / "broken.json").write_text("{not json at all", encoding="utf-8")
    (plans / "wrong-shape.json").write_text(json.dumps(["a", "list"]), encoding="utf-8")
    (plans / "bad-status.json").write_text(
        json.dumps({"name": "bad-status", "goal": "g",
                    "steps": [{"description": "x", "status": "weird"}]}),
        encoding="utf-8")

    store = PlanStore(plans)
    assert [p.name for p in store.list()] == ["good"]  # corrupted skipped, no crash
    assert store.load("good").goal == "fine"
    assert store.load("good").steps[0].description == "only step"


def test_load_corrupted_file_raises_value_error(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "broken.json").write_text("{not json at all", encoding="utf-8")
    with pytest.raises(ValueError):
        PlanStore(plans).load("broken")


def test_delete_removes_file_then_raises_when_missing(tmp_path):
    store = PlanStore(tmp_path / "plans")
    store.save(Plan(name="temp", goal="g"))
    store.delete("temp")
    assert not (tmp_path / "plans" / "temp.json").exists()
    with pytest.raises(ValueError):
        store.delete("temp")
    assert store.list() == []


# --- validation ----------------------------------------------------------------

@pytest.mark.parametrize("bad_name", ["", "Bad", "UPPER", "has space", "-lead", "_under", "dot.name", "café"])
def test_save_rejects_bad_names(tmp_path, bad_name):
    store = PlanStore(tmp_path / "plans")
    with pytest.raises(ValueError):
        store.save(Plan(name=bad_name, goal="g"))


@pytest.mark.parametrize("bad_name", ["Nope", "a/b"])
def test_load_and_delete_reject_bad_names(tmp_path, bad_name):
    store = PlanStore(tmp_path / "plans")
    with pytest.raises(ValueError):
        store.load(bad_name)
    with pytest.raises(ValueError):
        store.delete(bad_name)


def test_load_and_delete_raise_on_missing(tmp_path):
    store = PlanStore(tmp_path / "plans")
    with pytest.raises(ValueError):
        store.load("ghost")
    with pytest.raises(ValueError):
        store.delete("ghost")


@pytest.mark.parametrize("bad_status", ["", "Done", "complete", "skipped", None, 3])
def test_set_step_status_rejects_bad_status(tmp_path, bad_status):
    store = PlanStore(tmp_path / "plans")
    store.save(make_saved_two_step_plan())
    with pytest.raises(ValueError):
        store.set_step_status("deploy", 0, bad_status)
    # rejected before any write: the plan on disk is untouched
    assert store.load("deploy").steps[0].status == "pending"


@pytest.mark.parametrize("bad_index", [-1, 2, 99, "0", 1.0])
def test_set_step_status_rejects_bad_index(tmp_path, bad_index):
    store = PlanStore(tmp_path / "plans")
    store.save(make_saved_two_step_plan())
    with pytest.raises(ValueError):
        store.set_step_status("deploy", bad_index, "done")
    assert store.load("deploy").steps[0].status == "pending"


def make_saved_two_step_plan() -> Plan:
    return Plan(name="deploy", goal="ship it",
                steps=[PlanStep("build"), PlanStep("test")])


# --- set_step_status: persist updated_at + checkpoint --------------------------

def test_set_step_status_persists_updated_at_and_checkpoint(tmp_path):
    store = PlanStore(tmp_path / "plans")
    store.save(make_saved_two_step_plan())

    updated = store.set_step_status("deploy", 0, "done",
                                    checkpoint={"artifact": "app.exe"}, now=1234.5)
    assert updated.steps[0].status == "done"
    assert updated.steps[0].checkpoint == {"artifact": "app.exe"}
    assert updated.steps[0].updated_at == 1234.5
    assert updated.steps[1].updated_at == 0.0  # untouched step untouched

    persisted = store.load("deploy")
    assert persisted.steps[0].updated_at == 1234.5
    assert persisted.steps[0].checkpoint == {"artifact": "app.exe"}
    assert persisted.status == "in_progress"  # some done, one pending left


def test_set_step_status_keeps_checkpoint_when_none_given(tmp_path):
    store = PlanStore(tmp_path / "plans")
    store.save(Plan(name="crawl", goal="g", steps=[
        PlanStep("fetch", status="in_progress", checkpoint={"cursor": 7}, updated_at=5.0)]))

    updated = store.set_step_status("crawl", 0, "done", now=9.0)
    assert updated.steps[0].checkpoint == {"cursor": 7}
    assert updated.steps[0].updated_at == 9.0


# --- resumability --------------------------------------------------------------

def test_crash_resume_semantics(tmp_path):
    """The documented contract: after a crash, load() + next_step() pick up
    at the first in_progress or pending step with its checkpoint intact."""
    store = PlanStore(tmp_path / "plans")
    store.save(Plan(name="crawl", goal="crawl the docs site", created_at=1.0, steps=[
        PlanStep("fetch index", status="done", checkpoint={"urls": 10}, updated_at=10.0),
        PlanStep("fetch pages", status="in_progress",
                 checkpoint={"last_cursor": 57, "file": "pages.jsonl"}, updated_at=11.0),
        PlanStep("build search index"),
    ]))

    # a brand-new store over the same files = a process that crashed and restarted
    resumed = PlanStore(tmp_path / "plans").load("crawl")
    nxt = resumed.next_step()
    assert nxt is resumed.steps[1]
    assert nxt.checkpoint == {"last_cursor": 57, "file": "pages.jsonl"}
    assert resumed.status == "in_progress"
    assert resumed.progress() == (1, 3)

    # finish the in_progress step; the next resume continues at step 3
    store.set_step_status("crawl", 1, "done",
                          checkpoint={"last_cursor": 57, "file": "pages.jsonl"}, now=12.0)
    resumed2 = PlanStore(tmp_path / "plans").load("crawl")
    assert resumed2.next_step() is resumed2.steps[2]
    assert resumed2.next_step().checkpoint == {}
    assert resumed2.status == "in_progress"

    store.set_step_status("crawl", 2, "done", now=13.0)
    final = PlanStore(tmp_path / "plans").load("crawl")
    assert final.status == "done"
    assert final.next_step() is None
    assert final.progress() == (3, 3)


# --- ULTRON_HOME resolution ------------------------------------------------------

def test_plans_dir_follows_ultron_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path))
    assert ultron_home() == tmp_path
    assert plans_dir() == tmp_path / "plans"
    monkeypatch.delenv("ULTRON_HOME")
    assert ultron_home().name == ".ultron"
