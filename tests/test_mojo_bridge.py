"""Self-check for core/mojo_bridge.py response mapping. Monkeypatches subprocess.run
so this doesn't depend on a live WSL/Mojo install."""

from __future__ import annotations

import json
import subprocess

import pytest

from core.errors import BlockUnavailableError, CancelledError, ContractViolationError, DeadlineExceededError
from core.mojo_bridge import call_mojo_block
from core.run_context import RunContext


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch(monkeypatch, proc=None, raise_=None):
    def fake_run(cmd, input, capture_output, text, timeout):
        if raise_ is not None:
            raise raise_
        return proc

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_ok_response_returns_dict(monkeypatch):
    _patch(monkeypatch, proc=_FakeProc(stdout=json.dumps({"ok": True, "healthy": True, "detail": ""})))
    result = call_mojo_block(RunContext(), "fake_exe", {"method": "health"}, timeout_s=5.0)
    assert result == {"ok": True, "healthy": True, "detail": ""}


def test_cancelled_error_maps(monkeypatch):
    _patch(monkeypatch, proc=_FakeProc(stdout=json.dumps({"ok": False, "error": "cancelled", "detail": "x"})))
    with pytest.raises(CancelledError):
        call_mojo_block(RunContext(), "fake_exe", {"method": "generate"}, timeout_s=5.0)


def test_unknown_error_code_normalizes_to_block_unavailable(monkeypatch):
    _patch(monkeypatch, proc=_FakeProc(stdout=json.dumps({"ok": False, "error": "weird", "detail": "x"})))
    with pytest.raises(BlockUnavailableError):
        call_mojo_block(RunContext(), "fake_exe", {"method": "generate"}, timeout_s=5.0)


def test_nonzero_exit_raises_block_unavailable(monkeypatch):
    _patch(monkeypatch, proc=_FakeProc(returncode=1, stderr="boom"))
    with pytest.raises(BlockUnavailableError):
        call_mojo_block(RunContext(), "fake_exe", {"method": "health"}, timeout_s=5.0)


def test_non_json_stdout_raises_contract_violation(monkeypatch):
    _patch(monkeypatch, proc=_FakeProc(stdout="not json"))
    with pytest.raises(ContractViolationError):
        call_mojo_block(RunContext(), "fake_exe", {"method": "health"}, timeout_s=5.0)


def test_timeout_raises_deadline_exceeded(monkeypatch):
    _patch(monkeypatch, raise_=subprocess.TimeoutExpired(cmd="fake", timeout=5.0))
    with pytest.raises(DeadlineExceededError):
        call_mojo_block(RunContext(), "fake_exe", {"method": "health"}, timeout_s=5.0)
