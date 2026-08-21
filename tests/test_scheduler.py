"""Scheduler tests: due math and rescheduling with an injected fake clock,
exception capture (a failing job never kills the loop), and JobStore
persistence over a tmp file (round trip + corrupted JSON)."""

from __future__ import annotations

import json
import math

import pytest

from core.scheduler import (
    RESULT_LIMIT,
    Job,
    JobStore,
    Scheduler,
    schedules_path,
    ultron_home,
)


class FakeClock:
    """Deterministic clock: only moves when the test says so."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_scheduler(tmp_path, start: float = 1000.0) -> tuple[Scheduler, FakeClock, JobStore]:
    store = JobStore(tmp_path / "schedules.json")
    clock = FakeClock(start)
    return Scheduler(store, now=clock), clock, store


# --- validation ----------------------------------------------------------------

@pytest.mark.parametrize("bad_name", ["", "Bad", "UPPER", "has space", "-lead", "_under", "dot.name", "café"])
def test_add_job_rejects_bad_names(tmp_path, bad_name):
    sched, _, _ = make_scheduler(tmp_path)
    with pytest.raises(ValueError):
        sched.add_job(bad_name, "do a thing", every_s=60)


@pytest.mark.parametrize("bad_every", [0, -60, math.inf, math.nan, "60"])
def test_add_job_rejects_bad_every_s(tmp_path, bad_every):
    sched, _, _ = make_scheduler(tmp_path)
    with pytest.raises(ValueError):
        sched.add_job("ticker", "do a thing", every_s=bad_every)


def test_store_add_rejects_duplicate_and_bad_names(tmp_path):
    store = JobStore(tmp_path / "schedules.json")
    store.add(Job(name="ticker", prompt="p", every_s=60))
    with pytest.raises(ValueError):
        store.add(Job(name="ticker", prompt="p", every_s=60))
    with pytest.raises(ValueError):
        store.add(Job(name="Nope", prompt="p", every_s=60))


def test_store_update_and_remove_raise_on_missing(tmp_path):
    store = JobStore(tmp_path / "schedules.json")
    with pytest.raises(ValueError):
        store.update(Job(name="ghost", prompt="p", every_s=60))
    with pytest.raises(ValueError):
        store.remove("ghost")


# --- due math --------------------------------------------------------------------

def test_job_not_due_before_next_run_and_due_after(tmp_path):
    sched, clock, _ = make_scheduler(tmp_path, start=1000.0)
    sched.add_job("ticker", "check the feed", every_s=60, start_at=1100.0)

    clock.now = 1099.9
    assert sched.due() == []
    clock.now = 1100.0
    due = sched.due()
    assert [job.name for job in due] == ["ticker"]
    assert due[0].prompt == "check the feed"


def test_add_job_defaults_to_due_immediately(tmp_path):
    sched, _, _ = make_scheduler(tmp_path, start=1000.0)
    job = sched.add_job("ticker", "p", every_s=60)
    assert job.next_run == 1000.0
    assert sched.due() == [job]


def test_due_skips_disabled_jobs(tmp_path):
    sched, clock, store = make_scheduler(tmp_path, start=1000.0)
    job = sched.add_job("ticker", "p", every_s=60)
    store.update(Job(name=job.name, prompt=job.prompt, every_s=60, enabled=False))

    clock.now = 5000.0
    assert sched.due() == []


def test_due_sorted_by_next_run(tmp_path):
    sched, clock, _ = make_scheduler(tmp_path, start=1000.0)
    sched.add_job("late", "p", every_s=60, start_at=1200.0)
    sched.add_job("early", "p", every_s=60, start_at=1050.0)
    sched.add_job("middle", "p", every_s=60, start_at=1100.0)

    clock.now = 1300.0
    assert [job.name for job in sched.due()] == ["early", "middle", "late"]


# --- run_due ---------------------------------------------------------------------

def test_run_due_runs_reschedules_and_persists(tmp_path):
    sched, clock, store = make_scheduler(tmp_path, start=1000.0)
    sched.add_job("ticker", "check the feed", every_s=60, start_at=1000.0)
    seen: list[str] = []

    def runner(job: Job) -> str:
        seen.append(job.prompt)
        return "all good"

    clock.now = 1000.0
    results = sched.run_due(runner)
    assert seen == ["check the feed"]
    assert len(results) == 1 and results[0][1] is None

    job = results[0][0]
    assert job.last_run == 1000.0
    assert job.next_run == 1060.0
    assert job.last_result == "all good"

    # persisted: a brand-new store over the same file sees the new schedule
    reloaded = JobStore(tmp_path / "schedules.json").load()
    assert reloaded["ticker"].next_run == 1060.0
    assert reloaded["ticker"].last_result == "all good"

    # not due again until every_s elapses; then due once more
    clock.now = 1059.9
    assert sched.run_due(runner) == []
    clock.now = 1060.0
    assert len(sched.run_due(runner)) == 1
    assert store.load()["ticker"].next_run == 1120.0


def test_run_due_captures_exception_and_keeps_going(tmp_path):
    sched, clock, store = make_scheduler(tmp_path, start=1000.0)
    sched.add_job("broken", "p", every_s=60, start_at=1000.0)   # runs first
    sched.add_job("healthy", "p", every_s=60, start_at=1001.0)

    calls: list[str] = []

    def runner(job: Job) -> str:
        calls.append(job.name)
        if job.name == "broken":
            raise RuntimeError("boom")
        return "fine"

    clock.now = 1010.0
    results = sched.run_due(runner)  # must NOT raise

    # the failing job did not kill the loop: healthy still ran, in next_run order
    assert calls == ["broken", "healthy"]
    by_name = {job.name: (job, err) for job, err in results}
    assert by_name["broken"][1] == "boom"
    assert by_name["healthy"][1] is None

    # both were rescheduled and the error became the quick-display result
    persisted = store.load()
    assert persisted["broken"].last_run == 1010.0
    assert persisted["broken"].next_run == 1070.0
    assert persisted["broken"].last_result == "boom"
    assert persisted["healthy"].last_result == "fine"


def test_run_due_truncates_last_result(tmp_path):
    sched, clock, store = make_scheduler(tmp_path, start=1000.0)
    sched.add_job("chatty", "p", every_s=60)

    clock.now = 1000.0
    results = sched.run_due(lambda job: "x" * 1000)
    assert len(results[0][0].last_result) == RESULT_LIMIT == 280
    assert store.load()["chatty"].last_result == "x" * RESULT_LIMIT


def test_run_due_skips_disabled(tmp_path):
    sched, clock, store = make_scheduler(tmp_path, start=1000.0)
    job = sched.add_job("off", "p", every_s=60)
    store.update(Job(name=job.name, prompt="p", every_s=60, enabled=False))

    clock.now = 9999.0
    assert sched.run_due(lambda job: "should not run") == []


# --- persistence -----------------------------------------------------------------

def test_persistence_round_trip(tmp_path):
    path = tmp_path / "schedules.json"
    store = JobStore(path)
    store.add(Job(name="alpha", prompt="first", every_s=30, next_run=5.0, last_result="ok"))
    store.add(Job(name="beta", prompt="second", every_s=90, enabled=False, last_run=42.0))

    reloaded = JobStore(path).load()
    assert set(reloaded) == {"alpha", "beta"}
    assert reloaded["alpha"] == Job(name="alpha", prompt="first", every_s=30,
                                    next_run=5.0, last_result="ok")
    assert reloaded["beta"].enabled is False
    assert reloaded["beta"].last_run == 42.0
    assert [job.name for job in JobStore(path).list()] == ["alpha", "beta"]


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "schedules.json"
    store = JobStore(path)
    store.add(Job(name="ticker", prompt="p", every_s=60))
    store.save(store.load())  # overwrite path that already exists
    assert path.is_file()
    assert list(tmp_path.iterdir()) == [path]


def test_missing_file_loads_empty(tmp_path):
    assert JobStore(tmp_path / "nope" / "schedules.json").load() == {}


def test_corrupted_json_loads_empty(tmp_path):
    path = tmp_path / "schedules.json"
    path.write_text("{not json at all", encoding="utf-8")
    assert JobStore(path).load() == {}
    path.write_text(json.dumps(["a", "list"]), encoding="utf-8")  # wrong shape
    assert JobStore(path).load() == {}


def test_one_bad_entry_does_not_wipe_good_jobs(tmp_path):
    path = tmp_path / "schedules.json"
    good = Job(name="good", prompt="p", every_s=60, next_run=1.0).to_dict()
    bad = {"name": "bad", "every_s": -5}  # hand-edit typo: non-positive interval
    path.write_text(json.dumps({"good": good, "bad": bad}), encoding="utf-8")
    jobs = JobStore(path).load()
    assert set(jobs) == {"good"}
    assert jobs["good"].prompt == "p"


# --- ULTRON_HOME resolution --------------------------------------------------------

def test_schedules_path_follows_ultron_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path))
    assert ultron_home() == tmp_path
    assert schedules_path() == tmp_path / "schedules.json"
    monkeypatch.delenv("ULTRON_HOME")
    assert ultron_home().name == ".ultron"
