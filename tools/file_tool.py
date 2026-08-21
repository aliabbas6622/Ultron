"""FileTool brick: ToolProvider v1 over the local filesystem.

Grab-and-go: this package depends on contracts/ only. To embed ULTRON's file
tool in ANY agent, copy contracts/ + tools/ and execute policy-approved
file_write ActionIntents — no ULTRON runtime required.

Idempotency: action_id is the idempotency key; re-executing the same intent
writes the same content to the same path (safe retry semantics, 07).
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.action import ActionIntent
from contracts.errors import ContractViolationError
from contracts.health import HealthStatus
from contracts.tool import ToolDescriptor, ToolResult

FILE_WRITE_PARAMS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "absolute or workspace-relative target path"},
        "content": {"type": "string", "description": "full file content to write (UTF-8)"},
    },
    "required": ["path", "content"],
}


@dataclass
class FileTool:
    """Executes kind="file_write" intents. Overwrites the target atomically enough
    for V0.x (single write call); the runtime's Verifier confirms the effect.

    base_dir: relative paths resolve under it BEFORE policy evaluation, so an
    agent proposing "notes.txt" lands in the workspace, not the process CWD.
    Policy still gates the resolved absolute path — this is convenience, not a
    bypass."""

    block_id: str = "tool.file"
    base_dir: str | None = None

    def describe(self) -> list[ToolDescriptor]:
        note = f" Paths resolve under {self.base_dir}." if self.base_dir else ""
        return [
            ToolDescriptor(
                kind="file_write",
                name="file_write",
                risk_class="side_effect",
                description=f"Write content to a file path.{note}",
                params_schema=FILE_WRITE_PARAMS_SCHEMA,
            )
        ]

    def _resolve(self, path: str) -> str:
        import os

        if self.base_dir and not os.path.isabs(path):
            return os.path.join(self.base_dir, path)
        return path

    def execute(self, run, intent: ActionIntent) -> ToolResult:
        run.check_alive()
        if intent.kind != "file_write":
            raise ContractViolationError(f"{self.block_id}: cannot execute intent kind {intent.kind!r}")
        try:
            path = self._resolve(str(intent.params["path"]))
            content = intent.params["content"]
        except KeyError as exc:
            return ToolResult(
                ok=False, action_id=intent.action_id, kind=intent.kind,
                outputs={}, error=f"missing param: {exc}",
            )

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(
            ok=True,
            action_id=intent.action_id,
            kind=intent.kind,
            outputs={"path": path, "bytes_written": len(content.encode("utf-8"))},
        )

    def health(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail=f"{self.block_id}: local filesystem tool")
