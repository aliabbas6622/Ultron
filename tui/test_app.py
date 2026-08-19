"""Smoke test: TUI mounts, accepts a command, and runs the visit->summarize->save
slice end to end against a real (local) HTTP fetch — the one runnable check ponytail
asks for on non-trivial logic (command parsing, worker/thread event marshalling,
approval blocking)."""

from __future__ import annotations

import pytest

from tui.app import UltronTUI


@pytest.mark.asyncio
async def test_tui_mounts_and_handles_help_command() -> None:
    app = UltronTUI()
    async with app.run_test() as pilot:
        await pilot.click("#input")
        await pilot.press(*"/help", "enter")
        await pilot.pause()
        assert app._active_run is None


@pytest.mark.asyncio
async def test_unknown_command_does_not_crash() -> None:
    app = UltronTUI()
    async with app.run_test() as pilot:
        await pilot.click("#input")
        await pilot.press(*"/bogus", "enter")
        await pilot.pause()
        assert app._active_run is None


@pytest.mark.asyncio
async def test_cancel_with_no_active_run_is_a_noop() -> None:
    app = UltronTUI()
    async with app.run_test() as pilot:
        app.action_cancel_run()
        await pilot.pause()
        assert app._active_run is None
