"""ULTRON tool bricks. Depends on contracts/ only — grab-and-go for any host agent.

    from contracts.action import ActionIntent
    from tools.file_tool import FileTool
    from tools.file_verifier import FileVerifier

    tool, verifier = FileTool(), FileVerifier()
    intent = ActionIntent(kind="file_write", params={"path": "out.txt", "content": "hi"})
    result = tool.execute(host_ctx, intent)          # host_ctx: anything with check_alive()
    verifier.verify(host_ctx, intent, result)
"""

from __future__ import annotations
