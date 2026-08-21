"""Eval harness tests (08_ACCEPTANCE_AND_ROADMAP.md "Eval Harness — V0.1"):
deterministic fixtures grade pass/fail correctly, replay fixtures pin the
compiled prompt, and RunMetrics carries the required quality/efficiency fields.
"""

from __future__ import annotations

import json
import logging

from contracts.browser import PageProjection
from core.eval_harness import freeze, prompt_hash, run_scenario, summarize
from core.eval_harness import Scenario
from core.telemetry import RunMetrics, log_json

PAGE = PageProjection(
    url="https://example.com",
    title="Example Domain",
    text="This domain is for use in illustrative examples in documents.",
)


def _scenario(**kw) -> Scenario:
    defaults = dict(
        name="visit_summarize_save",
        url="https://example.com",
        page=PAGE,
        model_text="This page is an illustrative example domain.",
        model_output_tokens=8,
    )
    defaults.update(kw)
    return Scenario(**defaults)


def test_deterministic_fixture_passes_and_records_metrics(tmp_path):
    result = run_scenario(_scenario(), str(tmp_path))
    assert result.passed, result.failures
    m = result.metrics
    assert m.success and m.verification_ok is True
    assert m.model_calls == 1 and m.tool_calls == 1
    assert m.input_tokens > 0 and m.output_tokens == 8
    assert m.latency_s >= 0.0
    assert m.context_breakdown["text"] > 0 and m.context_breakdown["task"] > 0
    assert any("visit_summarize" in v for v in m.instruction_versions)
    assert (tmp_path / "summary.txt").read_text(encoding="utf-8") == "This page is an illustrative example domain."


def test_provider_unavailable_fixture_grades_as_failure_with_error_class(tmp_path):
    result = run_scenario(_scenario(model_error="block_unavailable", name="model_down"), str(tmp_path))
    assert result.passed, result.failures  # the *expectation* passed...
    assert result.metrics.success is False
    assert result.metrics.error_class == "BlockUnavailableError"
    assert not (tmp_path / "summary.txt").exists()


def test_policy_deny_fixture_expects_no_write(tmp_path):
    # deny via a policy the scenario can't satisfy: write target outside the workdir
    scenario = _scenario(name="deny_outside_dir", output_filename="../escaped.txt", expected_policy="deny")
    result = run_scenario(scenario, str(tmp_path))
    assert result.passed, result.failures
    assert not result.metrics.success
    import os

    assert not os.path.isfile(os.path.join(str(tmp_path), "..", "escaped.txt"))


def test_frozen_prompt_hash_is_a_replay_contract(tmp_path):
    frozen = freeze(_scenario(), str(tmp_path))
    assert frozen.expected_prompt_hash

    # replaying the frozen scenario still passes
    again = run_scenario(frozen, str(tmp_path))
    assert again.passed, again.failures

    # tampering with the compiled context (different page text => different prompt)
    # breaks the replay contract even though the run itself would still "succeed"
    tampered = _scenario(
        page=PageProjection(url="https://example.com", title="Example Domain", text="totally different text"),
        expected_prompt_hash=frozen.expected_prompt_hash,
    )
    result = run_scenario(tampered, str(tmp_path))
    assert not result.passed
    assert any("replay" in f for f in result.failures)


def test_summarize_reports_totals_and_metrics(tmp_path):
    results = [
        run_scenario(_scenario(), str(tmp_path)),
        run_scenario(_scenario(model_error="deadline_exceeded", name="timeout"), str(tmp_path)),
    ]
    summary = summarize(results)
    assert summary["total"] == 2 and summary["passed"] == 2
    assert summary["scenarios"] == {"visit_summarize_save": True, "timeout": True}
    assert summary["metrics"][1]["error_class"] == "DeadlineExceededError"


def test_run_metrics_defaults_and_log_json(tmp_path, caplog):
    metrics = RunMetrics(run_id="r1", trace_id="t1")
    d = metrics.to_dict()
    assert d["cached_tokens"] == 0 and d["retries"] == 0 and d["success"] is False
    assert isinstance(d["context_breakdown"], dict)

    with caplog.at_level(logging.INFO, logger="ultron"):
        log_json("run_finished", run_id="r1", input_tokens=42)
    line = [r for r in caplog.records if r.name == "ultron"][-1].getMessage()
    parsed = json.loads(line)
    assert parsed["event"] == "run_finished" and parsed["run_id"] == "r1" and parsed["input_tokens"] == 42


def test_prompt_hash_is_stable_and_short():
    h1, h2 = prompt_hash("abc"), prompt_hash("abc")
    assert h1 == h2 and len(h1) == 16 and prompt_hash("abd") != h1
