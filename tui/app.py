"""ULTRON TUI — terminal client for the V0.1 vertical slice.

Client block only: owns no runtime truth. It builds a RunContext + EventBus,
calls core.runtime.run_visit_summarize_save on a worker thread, and renders
whatever events/state come back. Model is tui.blocks.OllamaModel when a local
`ollama serve` is reachable, else the extractive StubSummarizerModel fallback
(see _select_model below). Swap for the vendor Mojo adapters/model once that's
bridged to Python — nothing else here should need to change (core.runtime is
the only import from the runtime side).

Run: python -m tui.app
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, RichLog, Static

from contracts.model import HealthStatus as ModelHealth
from core.action_intent import ActionIntent
from core.bus import EventBus
from core.errors import PolicyDeniedError, UltronError
from core.events import ModelCompleted, ModelStarted, PolicyDenied, ToolCompleted, ToolRequested
from core.policy import PolicyEngine
from core.run_context import RunContext
from core.runtime import run_visit_summarize_save
from tui.blocks import HttpBrowser, OllamaModel, StubSummarizerModel
from tools.file_tool import FileTool
from tools.file_verifier import FileVerifier


def _select_model():
    """Ollama if it's up and has the configured model pulled, else the extractive stub.
    ponytail: health-checked once per call, not cached — cheap local HTTP GET."""
    ollama = OllamaModel()
    if ollama.health().healthy:
        return ollama
    return StubSummarizerModel()

RUNS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tui_runs")

_HEALTH_COLOR = {True: "green", False: "red"}


@dataclass
class _RunView:
    """UI-local snapshot of the active run. Not runtime truth — derived from events."""

    run_id: str = ""
    url: str = ""
    model_block: str = ""
    started_at: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    writes: int = 0
    status: str = "idle"


class ApprovalModal(ModalScreen[bool]):
    """Blocks the active run until the user approves/denies an ActionIntent."""

    BINDINGS = [
        Binding("a", "approve", "Approve"),
        Binding("d", "deny", "Deny"),
        Binding("escape", "deny", "Deny"),
    ]

    DEFAULT_CSS = """
    ApprovalModal { align: center middle; }
    #box {
        width: 60; height: auto; border: thick $warning; padding: 1 2;
        background: $surface;
    }
    #box Label { margin-bottom: 1; }
    #buttons { align: center middle; height: 3; }
    #buttons Button { margin: 0 1; }
    """

    def __init__(self, intent: ActionIntent) -> None:
        super().__init__()
        self._intent = intent

    def compose(self) -> ComposeResult:
        path = self._intent.params.get("path", "?")
        body = (
            f"[b yellow]Approval Required[/]\n\n{self._intent.kind}\n\n"
            f"Write to: {path}\n\nRisk: LOW  (single sandboxed text file)"
        )
        with Grid(id="box"):
            yield Label(body)
            with Grid(id="buttons"):
                yield Button("[A]pprove", id="approve", variant="success")
                yield Button("[D]eny", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


class UltronTUI(App[None]):
    TITLE = "ULTRON"

    BINDINGS = [
        Binding("ctrl+l", "focus_input", "Input"),
        Binding("ctrl+t", "focus_trace", "Trace"),
        Binding("ctrl+b", "focus_blocks", "Blocks"),
        Binding("ctrl+k", "cancel_run", "Cancel run"),
        Binding("question_mark", "help", "Help", key_display="?"),
    ]

    DEFAULT_CSS = """
    Screen { layout: vertical; background: $background; }
    #status {
        dock: top; height: 1; background: $panel; color: $text;
        padding: 0 1;
    }
    #body {
        layout: grid; grid-size: 2 2; grid-rows: 2fr 1fr; grid-columns: 2fr 1fr;
        height: 1fr;
    }
    #conversation { border: round $primary; }
    #operation { border: round $primary; padding: 0 1; }
    #blocks { border: round $primary; padding: 0 1; }
    #trace { border: round $primary; }
    Input { dock: bottom; }
    """

    def __init__(self) -> None:
        super().__init__()
        os.makedirs(RUNS_DIR, exist_ok=True)
        self._active_run: RunContext | None = None
        self._view = _RunView()
        self._start_time = 0.0

    def compose(self) -> ComposeResult:
        yield Static(self._status_text(), id="status")
        with Grid(id="body"):
            yield RichLog(id="conversation", wrap=True, highlight=True, markup=True)
            yield Static(self._operation_text(), id="operation")
            yield Static(self._blocks_text(), id="blocks")
            yield RichLog(id="trace", wrap=True, markup=True)
        yield Input(placeholder="URL to visit, or /help", id="input")
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#conversation", RichLog)
        log.write("[b]ULTRON[/b] V0.1 — type a URL to run visit -> summarize -> save, or /help.")
        self.query_one("#input", Input).focus()

    # -- rendering helpers ----------------------------------------------

    def _status_text(self) -> str:
        v = self._view
        elapsed = f"{time.time() - self._start_time:.1f}s" if self._start_time else "-"
        return (
            f"[b]ULTRON[/b] local-node | {v.status.upper()} | model: {v.model_block or '-'} | "
            f"{v.input_tokens + v.output_tokens} tok | writes: {v.writes} | elapsed: {elapsed}"
        )

    def _operation_text(self) -> str:
        v = self._view
        if not v.run_id:
            return "[dim]Active Operation[/dim]\n\nNo run active."
        return (
            "[b]Active Operation[/b]\n\n"
            f"run     {v.run_id}\n"
            f"task    visit -> summarize -> save\n"
            f"url     {v.url}\n"
            f"model   {v.model_block or '-'}\n"
            f"in tok  {v.input_tokens}\n"
            f"out tok {v.output_tokens}\n"
            f"writes  {v.writes}\n"
            f"status  {v.status}"
        )

    def _blocks_text(self) -> str:
        browser_h = HttpBrowser().health()
        model = _select_model()
        model_h: ModelHealth = model.health()
        b_color = _HEALTH_COLOR[browser_h.healthy]
        m_color = _HEALTH_COLOR[model_h.healthy]
        return (
            "[b]Blocks / Health[/b]\n\n"
            f"browser.http_stub    [{b_color}]{'healthy' if browser_h.healthy else 'unhealthy'}[/{b_color}]  "
            f"({browser_h.detail})\n"
            f"model.{model.block_id:<13} [{m_color}]{'healthy' if model_h.healthy else 'unhealthy'}[/{m_color}]  "
            f"({model_h.detail})"
        )

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_text())
        self.query_one("#operation", Static).update(self._operation_text())

    # -- input / commands -------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._start_run(text)

    def _handle_command(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        log = self.query_one("#conversation", RichLog)
        if cmd in ("/quit", "/q"):
            self.exit()
        elif cmd == "/cancel":
            self.action_cancel_run()
        elif cmd == "/clear":
            log.clear()
        elif cmd == "/blocks" or cmd == "/health":
            self.query_one("#blocks", Static).update(self._blocks_text())
            log.write("[dim]blocks/health refreshed[/dim]")
        elif cmd == "/trace":
            self.action_focus_trace()
        elif cmd == "/help":
            log.write(
                "[b]Commands[/b]  /help /blocks /health /trace /cancel /clear /quit\n"
                "[b]Keys[/b]      ctrl+l input  ctrl+t trace  ctrl+b blocks  ctrl+k cancel run\n"
                "Type a URL (http/https) to run: visit -> summarize -> save."
            )
        else:
            log.write(f"[red]unknown command:[/red] {cmd}")

    # -- run lifecycle ------------------------------------------------------

    def _start_run(self, url: str) -> None:
        if self._active_run is not None:
            self.query_one("#conversation", RichLog).write("[yellow]a run is already active — /cancel it first[/yellow]")
            return

        run = RunContext()
        bus = EventBus()
        self._active_run = run
        self._start_time = time.time()
        self._view = _RunView(run_id=run.run_id, url=url, status="running")
        self._refresh_status()

        log = self.query_one("#conversation", RichLog)
        log.write(f"[b]>[/b] {url}")
        trace = self.query_one("#trace", RichLog)
        trace.write(f"[dim]{time.strftime('%H:%M:%S')}[/dim] run.started {run.run_id}")

        bus.subscribe(ModelStarted, lambda e: self.call_from_thread(self._on_event, e))
        bus.subscribe(ModelCompleted, lambda e: self.call_from_thread(self._on_event, e))
        bus.subscribe(ToolRequested, lambda e: self.call_from_thread(self._on_event, e))
        bus.subscribe(ToolCompleted, lambda e: self.call_from_thread(self._on_event, e))
        bus.subscribe(PolicyDenied, lambda e: self.call_from_thread(self._on_event, e))

        self.run_worker(lambda: self._do_run(run, bus, url), thread=True, name="ultron-run")

    def _do_run(self, run: RunContext, bus: EventBus, url: str) -> None:
        policy = PolicyEngine(allowed_write_dir=RUNS_DIR)
        output_path = os.path.join(RUNS_DIR, f"{run.run_id}.txt")
        try:
            result = run_visit_summarize_save(
                run=run,
                bus=bus,
                browser=HttpBrowser(),
                model=_select_model(),
                policy=policy,
                tool=FileTool(),
                verifier=FileVerifier(),
                url=url,
                output_path=output_path,
                on_action_intent=self._request_approval,
            )
            self.call_from_thread(self._on_run_done, result.summary, result.output_path, None)
        except UltronError as exc:
            self.call_from_thread(self._on_run_done, None, None, exc)
        except Exception as exc:  # noqa: BLE001 — surface any unexpected failure to the UI, don't crash the app
            self.call_from_thread(self._on_run_done, None, None, exc)
        finally:
            self._active_run = None

    def _request_approval(self, intent: ActionIntent) -> bool:
        """Called on the worker thread by core.runtime. Blocks until the user answers."""
        done = threading.Event()
        holder: dict[str, bool] = {}

        def _show() -> None:
            def _callback(approved: bool | None) -> None:
                holder["v"] = bool(approved)
                done.set()

            self.push_screen(ApprovalModal(intent), _callback)

        self.call_from_thread(_show)
        done.wait()
        return holder.get("v", False)

    def _on_event(self, event: object) -> None:
        trace = self.query_one("#trace", RichLog)
        ts = time.strftime("%H:%M:%S")
        v = self._view
        if isinstance(event, ModelStarted):
            v.model_block = event.block_id
            trace.write(f"[dim]{ts}[/dim] model.started {event.block_id}")
        elif isinstance(event, ModelCompleted):
            v.input_tokens = event.input_tokens
            v.output_tokens = event.output_tokens
            trace.write(f"[dim]{ts}[/dim] model.completed in={event.input_tokens} out={event.output_tokens}")
        elif isinstance(event, ToolRequested):
            trace.write(f"[dim]{ts}[/dim] tool.requested {event.tool_name}")
        elif isinstance(event, ToolCompleted):
            v.writes += 1
            trace.write(f"[dim]{ts}[/dim] tool.completed ok={event.ok}")
        elif isinstance(event, PolicyDenied):
            trace.write(f"[dim]{ts}[/dim] [red]policy.denied[/red] {event.reason}")
        self._refresh_status()

    def _on_run_done(self, summary: str | None, output_path: str | None, error: Exception | None) -> None:
        log = self.query_one("#conversation", RichLog)
        trace = self.query_one("#trace", RichLog)
        ts = time.strftime("%H:%M:%S")
        if error is not None:
            status = "denied" if isinstance(error, PolicyDeniedError) else "error"
            self._view.status = status
            log.write(f"[red]{type(error).__name__}:[/red] {error}")
            trace.write(f"[dim]{ts}[/dim] run.failed {type(error).__name__}")
        else:
            self._view.status = "completed"
            log.write(f"[green]{summary}[/green]\n[dim]saved -> {output_path}[/dim]")
            trace.write(f"[dim]{ts}[/dim] run.completed")
        self._refresh_status()

    # -- actions --------------------------------------------------------

    def action_focus_input(self) -> None:
        self.query_one("#input", Input).focus()

    def action_focus_trace(self) -> None:
        self.query_one("#trace", RichLog).focus()

    def action_focus_blocks(self) -> None:
        self.query_one("#blocks", Static).focus()

    def action_cancel_run(self) -> None:
        if self._active_run is None:
            self.query_one("#conversation", RichLog).write("[dim]no active run[/dim]")
            return
        self.query_one("#conversation", RichLog).write("[yellow]Cancelling...[/yellow]")
        self._active_run.cancel()

    def action_help(self) -> None:
        self._handle_command("/help")


def main() -> None:
    UltronTUI().run()


if __name__ == "__main__":
    main()
