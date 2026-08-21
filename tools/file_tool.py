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
    for V0.x (single write call); the runtime's Verifier confirms the effect."""

    block_id: str = "tool.file"
    allowed_dir: str | None = None  # optional second layer; policy is the real gate

    def describe(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                kind="file_write",
                name="file_write",
                risk_class="side_effect",
                description="Write content to a file path.",
                params_schema=FILE_WRITE_PARAMS_SCHEMA,
            )
        ]

    def execute(self, run, intent: ActionIntent) -> ToolResult:
        run.check_alive()
        if intent.kind != "file_write":
            raise ContractViolationError(f"{self.block_id}: cannot execute intent kind {intent.kind!r}")
        try:
            path = intent.params["path"]
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
