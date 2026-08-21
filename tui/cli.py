"""ULTRON terminal entrypoint.

    ultron                      -> interactive TUI
    ultron run <url> [-o FILE]  -> headless vertical slice: visit, summarize, save
    ultron health               -> block health report
    ultron version              -> version + contract list

Installed as the `ultron` console script (pyproject [project.scripts]). The
headless path is the same composition as the TUI — blocks injected behind
contracts — plus structured event logs on stderr and a metrics line on stdout.
"""

from __future__ import annotations

import argparse
import os
import sys

from core.bus import EventBus
from core.errors import UltronError
from core.policy import PolicyEngine
from core.run_context import RunContext
from core.runtime import run_visit_summarize_save
from core.telemetry import MetricsRecorder, attach_bus_logger
from tools.file_tool import FileTool
from tools.file_verifier import FileVerifier
from tui import __version__
from tui.blocks import HttpBrowser, select_model


def _cmd_run(args: argparse.Namespace) -> int:
    output_path = os.path.abspath(args.output or "summary.txt")
    out_dir = os.path.dirname(output_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    run = RunContext()
    bus = EventBus()
    attach_bus_logger(bus)  # structured JSON events on the ultron logger (stderr)
    recorder = MetricsRecorder(bus, run)
    model = select_model(args.model)
    print(f"model: {model.block_id} | browser: http | writing: {output_path}", file=sys.stderr)

    try:
        result = run_visit_summarize_save(
            run=run,
            bus=bus,
            browser=HttpBrowser(),
            model=model,
            policy=PolicyEngine(allowed_write_dir=out_dir),
            tool=FileTool(),
            verifier=FileVerifier(),
            url=args.url,
            output_path=output_path,
            timeout_s=args.timeout,
        )
    except UltronError as exc:
        metrics = recorder.finish(error=exc)
        print(f"FAILED [{type(exc).__name__}]: {exc}", file=sys.stderr)
        print(
            f"run: {metrics.run_id} | latency: {metrics.latency_s:.2f}s | model calls: {metrics.model_calls}",
            file=sys.stderr,
        )
        return 1

    metrics = recorder.finish()
    print(result.summary)
    print(
        f"saved: {result.output_path} | tokens in/out: {metrics.input_tokens}/{metrics.output_tokens}"
        f" | latency: {metrics.latency_s:.2f}s | verified: {metrics.verification_ok}",
        file=sys.stderr,
    )
    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    browser = HttpBrowser()
    model = select_model(args.model)
    verifier = FileVerifier()

    rows = [
        (browser.block_id, browser.health().healthy, browser.health().detail),
        (f"model.{model.block_id}", model.health().healthy, model.health().detail),
        ("tool.file", FileTool().health().healthy, FileTool().health().detail),
        (verifier.block_id, True, f"verifies kind: {verifier.supports_kind}"),
    ]
    width = max(len(name) for name, _, _ in rows)
    all_healthy = True
    for name, healthy, detail in rows:
        all_healthy &= healthy
        print(f"[{'OK ' if healthy else 'BAD'}] {name:<{width}}  {detail}")
    return 0 if all_healthy else 1


def _cmd_version(args: argparse.Namespace) -> int:
    from contracts import browser, memory, model, policy, tool, verification

    print(f"ultron {__version__}")
    for mod in (model, browser, tool, verification, policy, memory):
        print(f"  {mod.CONTRACT_ID} v{mod.CONTRACT_VERSION}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ultron",
        description="ULTRON personal AI runtime (V0.x vertical slice). No args = interactive TUI.",
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="visit a URL, summarize in one sentence, save to a file")
    p_run.add_argument("url", help="http(s) URL to visit")
    p_run.add_argument("-o", "--output", default=None, help="output file (default: ./summary.txt)")
    p_run.add_argument(
        "--model", default=None,
        help="Ollama model name (default: $OLLAMA_MODEL or llama3.2; falls back to the extractive stub)",
    )
    p_run.add_argument("--timeout", type=float, default=30.0, help="browser timeout seconds")
    p_run.set_defaults(func=_cmd_run)

    p_health = sub.add_parser("health", help="report block health")
    p_health.add_argument("--model", default=None, help="Ollama model name to check")
    p_health.set_defaults(func=_cmd_health)

    p_version = sub.add_parser("version", help="version + shipped contracts")
    p_version.set_defaults(func=_cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:  # bare `ultron` -> TUI
        from tui.app import UltronTUI

        UltronTUI().run()
        return 0

    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
