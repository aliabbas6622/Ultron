"""Generic transport for calling a Mojo adapter block from core.

Mojo only runs inside WSL2 on this project's dev host, so core (Python) can
never import a block in-process - the bridge is a subprocess regardless of
language. Each block exposes a CLI entrypoint that does one JSON
request/response round trip over stdio. See 03_BLOCK_CONTRACT.md
"Mojo Adapter Bridge" for the wire shapes. This module is transport only;
it does not know about any specific block's contract.
"""

from __future__ import annotations

import json
import subprocess
import time

from core.errors import (
    BlockUnavailableError,
    CancelledError,
    ContractViolationError,
    DeadlineExceededError,
)
from core.run_context import RunContext

WSL_DISTRO = "Debian"
VENV_ACTIVATE = "source .venv/bin/activate"

_ERROR_TYPES = {
    "cancelled": CancelledError,
    "deadline_exceeded": DeadlineExceededError,
}


def call_mojo_block(run: RunContext, executable: str, request: dict, timeout_s: float) -> dict:
    """One request/response round trip against a Mojo block's CLI entrypoint.

    executable: shell command that runs the block's CLI (e.g. "mojo run adapters/model/deepseek.mojo").
    request: JSON-serializable dict; must include "method".
    Returns the response dict on {"ok": true}; raises a normalized core.errors
    exception otherwise.
    """
    run.check_alive()
    if run.deadline_at is not None:
        timeout_s = min(timeout_s, max(0.0, run.deadline_at - time.time()))

    cmd = ["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc", f"{VENV_ACTIVATE} && {executable}"]
    try:
        proc = subprocess.run(
            cmd, input=json.dumps(request), capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        # ponytail: kills our wait, not guaranteed to kill the WSL-side process tree.
        # Add explicit process-group teardown if orphaned mojo processes show up.
        raise DeadlineExceededError(f"mojo block {executable!r} exceeded {timeout_s}s") from e

    if proc.returncode != 0:
        raise BlockUnavailableError(f"mojo block {executable!r} exited {proc.returncode}: {proc.stderr.strip()}")

    try:
        response = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        raise ContractViolationError(f"mojo block {executable!r} returned non-JSON stdout: {proc.stdout!r}") from e

    if not response.get("ok", False):
        error_code = response.get("error", "block_unavailable")
        detail = response.get("detail", "") or f"mojo block {executable!r} reported {error_code!r}"
        raise _ERROR_TYPES.get(error_code, BlockUnavailableError)(detail)

    return response
