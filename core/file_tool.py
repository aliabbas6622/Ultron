"""FileTool: executes a policy-approved file_write ActionIntent."""

from __future__ import annotations

from dataclasses import dataclass

from core.action_intent import ActionIntent


@dataclass(frozen=True)
class FileWriteResult:
    action_id: str
    path: str
    bytes_written: int


def execute_file_write(intent: ActionIntent) -> FileWriteResult:
    if intent.kind != "file_write":
        raise ValueError(f"file_tool cannot execute intent kind: {intent.kind}")

    path = intent.params["path"]
    content = intent.params["content"]
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return FileWriteResult(action_id=intent.action_id, path=path, bytes_written=len(content.encode("utf-8")))
