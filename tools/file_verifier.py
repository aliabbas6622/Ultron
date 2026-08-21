"""FileVerifier brick: Verifier v1 that confirms file_write side effects against
the filesystem itself — the ToolResult's ok=True is never trusted (05)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from contracts.action import ActionIntent
from contracts.tool import ToolResult
from contracts.verification import VerificationOutcome


@dataclass
class FileVerifier:
    supports_kind: str = "file_write"
    block_id: str = "verifier.file"

    def verify(self, run, intent: ActionIntent, result: ToolResult) -> VerificationOutcome:
        path = result.outputs.get("path") or intent.params.get("path")
        if not path or not os.path.isfile(path):
            return VerificationOutcome(verified=False, detail=f"file not found after write: {path}")
        with open(path, encoding="utf-8") as f:
            actual = f.read()
        expected = intent.params.get("content", "")
        if actual != expected:
            return VerificationOutcome(verified=False, detail=f"file content mismatch at {path}")
        return VerificationOutcome(verified=True, detail=f"verified {len(actual)} chars at {path}")
