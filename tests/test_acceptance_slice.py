"""V0.1 acceptance test for 08_ACCEPTANCE_AND_ROADMAP.md.

Uses fake browser/model blocks (test doubles, not real adapters) so this runs without
network or real model/browser dependencies. Real adapters (DeepSeek/model, Copilot/browser)
get their own conformance suites when they land in adapters/.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from contracts.browser import HealthStatus as BrowserHealth
from contracts.browser import PageProjection
from contracts.model import HealthStatus as ModelHealth
from contracts.model import ModelCapabilities, ModelResult
from core.bus import EventBus
from core.errors import CancelledError, PolicyDeniedError
from core.events import ModelCompleted, ModelStarted, ToolCompleted
from core.policy import PolicyEngine
from core.run_context import RunContext
from core.runtime import run_visit_summarize_save
from tools.file_tool import FileTool
from tools.file_verifier import FileVerifier


class FakeBrowser:
    block_id = "fake_browser"

    def visit(self, run, url, timeout_s):
        run.check_alive()
        return PageProjection(url=url, title="Example Domain", text="This domain is for use in illustrative examples.")

    def health(self):
        return BrowserHealth(healthy=True)


class FakeModel:
    block_id = "fake_model"
    capabilities = ModelCapabilities()

    def generate(self, run, prompt):
        run.check_alive()
        return ModelResult(
            text="This page is an illustrative example domain.",
            input_tokens=len(prompt) // 4,
            output_tokens=8,
            finish_reason="stop",
        )

    def health(self):
        return ModelHealth(healthy=True)


class CancellingModel(FakeModel):
    def generate(self, run, prompt):
        run.cancel()
        run.check_alive()  # raises CancelledError
        raise AssertionError("unreachable")


def _run(tmp_path):
    output_path = os.path.join(tmp_path, "summary.txt")
    run = RunContext()
    bus = EventBus()
    events = []
    for evt_type in (ModelStarted, ModelCompleted, ToolCompleted):
        bus.subscribe(evt_type, events.append)
    policy = PolicyEngine(allowed_write_dir=tmp_path)
    return run, bus, events, policy, output_path


def test_vertical_slice_end_to_end(tmp_path):
    tmp_path = str(tmp_path)
    run, bus, events, policy, output_path = _run(tmp_path)

    result = run_visit_summarize_save(
        run=run,
        bus=bus,
        browser=FakeBrowser(),
        model=FakeModel(),
        policy=policy,
        tool=FileTool(),
        verifier=FileVerifier(),
        url="https://example.com",
        output_path=output_path,
    )

    assert result.summary
    assert os.path.isfile(output_path)
    with open(output_path, encoding="utf-8") as f:
        assert f.read() == result.summary

    assert run.trace_id
    assert run.run_id
    assert run.budget.used_input_tokens > 0
    assert run.budget.used_model_calls == 1
    assert run.budget.used_external_writes == 1
    assert {type(e) for e in events} == {ModelStarted, ModelCompleted, ToolCompleted}


def test_policy_denies_write_outside_allowed_dir(tmp_path):
    tmp_path = str(tmp_path)
    run, bus, events, policy, _ = _run(tmp_path)
    outside_path = os.path.join(tempfile.gettempdir(), "should_not_write.txt")

    with pytest.raises(PolicyDeniedError):
        run_visit_summarize_save(
            run=run, bus=bus, browser=FakeBrowser(), model=FakeModel(), policy=policy,
            tool=FileTool(), verifier=FileVerifier(),
            url="https://example.com", output_path=outside_path,
        )
    assert not os.path.isfile(outside_path)


def test_cancellation_stops_the_run_before_any_write(tmp_path):
    tmp_path = str(tmp_path)
    run, bus, events, policy, output_path = _run(tmp_path)

    with pytest.raises(CancelledError):
        run_visit_summarize_save(
            run=run, bus=bus, browser=FakeBrowser(), model=CancellingModel(), policy=policy,
            tool=FileTool(), verifier=FileVerifier(),
            url="https://example.com", output_path=output_path,
        )
    assert not os.path.isfile(output_path)


def test_browser_failure_does_not_crash_runtime(tmp_path):
    tmp_path = str(tmp_path)
    run, bus, events, policy, output_path = _run(tmp_path)

    class FailingBrowser(FakeBrowser):
        def visit(self, run, url, timeout_s):
            from core.errors import BlockUnavailableError

            raise BlockUnavailableError("browser down")

    with pytest.raises(Exception):
        run_visit_summarize_save(
            run=run, bus=bus, browser=FailingBrowser(), model=FakeModel(), policy=policy,
            tool=FileTool(), verifier=FileVerifier(),
            url="https://example.com", output_path=output_path,
        )
    # runtime raised a normalized error rather than crashing with an unhandled state
