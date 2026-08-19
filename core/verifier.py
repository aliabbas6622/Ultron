"""Verification: 'done' from the model is not proof. Confirm side effects against external evidence."""

from __future__ import annotations

import os

from core.errors import VerificationFailedError
from core.file_tool import FileWriteResult


def verify_file_write(result: FileWriteResult, expected_content: str) -> None:
    if not os.path.isfile(result.path):
        raise VerificationFailedError(f"file not found after write: {result.path}")

    with open(result.path, encoding="utf-8") as f:
        actual = f.read()

    if actual != expected_content:
        raise VerificationFailedError(f"file content mismatch at {result.path}")
