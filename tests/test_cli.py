"""CLI tests: the installed `ultron` entry point works headless — success path,
normalized-failure exit codes, health and version output. Network blocks are
monkeypatched so these run offline; the real network path is exercised manually
(`ultron run https://example.com`)."""

from __future__ import annotations

import os

import pytest
from contracts.browser import HealthStatus as BrowserHealth
from contracts.browser import PageProjection
from contracts.errors import BlockUnavailableError

from tui import cli


class OfflineBrowser:
    block_id = "offline_browser"

    def visit(self, run, url, timeout_s):
        run.check_alive()
        return PageProjection(url=url, title="Example Domain",
                              text="This domain is for use in illustrative examples.")

    def health(self):
        return BrowserHealth(healthy=True, detail="offline double")


class OfflineModel:
    block_id = "offline_model"

    def health(self):
        from contracts.model import HealthStatus

        return HealthStatus(healthy=True, detail="offline double")

    def generate(self, run, prompt):
        from contracts.model import ModelResult

        run.check_alive()
        return ModelResult(text="An illustrative example domain.", input_tokens=10, output_tokens=6,
                           finish_reason="stop")


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(cli, "HttpBrowser", OfflineBrowser)
    monkeypatch.setattr(cli, "select_model", lambda model_name=None: OfflineModel())


def test_run_saves_summary_and_exits_zero(tmp_path, offline, capsys):
    out = tmp_path / "summary.txt"
    rc = cli.main(["run", "https://example.com", "-o", str(out)])
    assert rc == 0
    assert out.read_text(encoding="utf-8") == "An illustrative example domain."
    captured = capsys.readouterr()
    assert "An illustrative example domain." in captured.out
    assert "verified: True" in captured.err


def test_run_browser_failure_is_normalized_to_exit_one(tmp_path, offline, monkeypatch, capsys):
    class DownBrowser(OfflineBrowser):
        def visit(self, run, url, timeout_s):
            raise BlockUnavailableError("network down")

    monkeypatch.setattr(cli, "HttpBrowser", DownBrowser)
    out = tmp_path / "summary.txt"
    rc = cli.main(["run", "https://example.com", "-o", str(out)])
    assert rc == 1
    assert not out.exists()
    assert "BlockUnavailableError" in capsys.readouterr().err


def test_health_reports_blocks(offline, capsys):
    assert cli.main(["health"]) == 0
    out = capsys.readouterr().out
    assert "offline_browser" in out and "tool.file" in out and "verifier.file" in out


def test_version_lists_shipped_contracts(capsys):
    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    for contract in ("model_provider", "browser_provider", "tool_provider", "verifier",
                     "policy_evaluator", "memory_provider"):
        assert contract in out
