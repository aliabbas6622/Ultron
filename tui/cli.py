"""ULTRON terminal entrypoint.

    ultron                          -> interactive TUI (free chat + URL runs)
    ultron run <url> [-o FILE]      -> headless vertical slice: visit, summarize, save
    ultron ask "..."                -> one-shot question to the default provider
    ultron chat                     -> multi-turn REPL with persistent memory
    ultron providers <cmd>          -> add/edit/remove/list/default/test providers
    ultron health                   -> block health report
    ultron version                  -> version + contract list

Providers are configured in <ULTRON_HOME>/providers.toml (default ~/.ultron);
secrets live in the SecretStore (secrets.json), never in the config file.
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
from providers.registry import KINDS, ProviderRegistry
from tools.file_tool import FileTool
from tools.file_verifier import FileVerifier
from tui import __version__
from tui.blocks import HttpBrowser, select_model

MEMORY_DB = os.path.join(os.environ.get("ULTRON_HOME", os.path.join(os.path.expanduser("~"), ".ultron")), "memory.db")


def _resolve_model(model_name: str | None = None):
    return select_model(model_name)


def _open_memory():
    try:
        from core.memory_sqlite import SqliteMemoryStore

        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        return SqliteMemoryStore(MEMORY_DB)
    except Exception:  # noqa: BLE001 — memory is optional; chat works without it
        return None


def _cmd_run(args: argparse.Namespace) -> int:
    output_path = os.path.abspath(args.output or "summary.txt")
    out_dir = os.path.dirname(output_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    run = RunContext()
    bus = EventBus()
    attach_bus_logger(bus)  # structured JSON events on the ultron logger (stderr)
    recorder = MetricsRecorder(bus, run)
    model = _resolve_model(args.model)
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


def _cmd_ask(args: argparse.Namespace) -> int:
    from core.chat import chat_turn

    run = RunContext()
    model = _resolve_model(args.model)
    memory = _open_memory() if not args.no_memory else None
    try:
        answer = chat_turn(run, model, [], args.text, memory=memory, system=args.system)
    except UltronError as exc:
        print(f"FAILED [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 1
    finally:
        if memory is not None:
            memory.close()
    print(answer.text)
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    from core.chat import ChatMessage, chat_turn

    registry = ProviderRegistry.load()
    model = _resolve_model(args.model)
    memory = _open_memory() if not args.no_memory else None
    history: list[ChatMessage] = []
    run = RunContext()
    who = f"{model.block_id}"
    if registry.default_provider:
        who += f" (default: {registry.default_provider}"
        if registry.default_model:
            who += f"/{registry.default_model}"
        who += ")"
    print(f"ULTRON chat — {who}. /exit to quit, /clear to reset, /memory to toggle recall.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text == "/exit":
            return 0
        if text == "/clear":
            history.clear()
            print("(history cleared; long-term memory persists)")
            continue
        if text == "/memory":
            if memory is None:
                print("memory unavailable — continuing without recall")
            else:
                from contracts.memory import MemoryQuery

                for record in memory.retrieve(run, MemoryQuery(kind="episodic", subject="chat", limit=5)):
                    print(f"  {record.observed_at:.0f}s ago: {record.value[:160]}")
            continue
        try:
            answer = chat_turn(run, model, history, text, memory=memory)
        except UltronError as exc:
            print(f"[{type(exc).__name__}] {exc}")
            continue
        history.append(ChatMessage(role="user", text=text))
        history.append(answer)
        print(f"ultron> {answer.text}")
        if memory is not None:
            memory.close()
            memory = _open_memory()  # one connection per turn keeps the REPL reentrant


def _registry() -> ProviderRegistry:
    return ProviderRegistry.load()


def _cmd_providers(args: argparse.Namespace) -> int:
    reg = _registry()
    cmd = args.providers_cmd

    if cmd == "list":
        print(f"config: {reg.config_path}")
        print(f"default: {reg.default_provider or '-'} / {reg.default_model or reg.default_model_name() or '-'}\n")
        header = f"{'NAME':<18} {'KIND':<18} {'MODEL':<28} {'AUTH':<8} BASE_URL"
        print(header)
        for name, spec in reg.specs.items():
            marker = "*" if name == reg.default_provider else " "
            print(f"{marker}{spec.to_row()}")
        return 0

    if cmd == "add":
        try:
            spec = reg.add(args.name, args.kind, args.base_url, args.model,
                           api_key=args.api_key, set_default=args.set_default)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"added {spec.name} ({spec.kind}) @ {spec.base_url} model={spec.model or '-'}")
        return 0

    if cmd == "edit":
        try:
            spec = reg.edit(args.name, base_url=args.base_url, model=args.model, api_key=args.api_key, kind=args.kind)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"edited {spec.name}: kind={spec.kind} model={spec.model or '-'} @ {spec.base_url}")
        return 0

    if cmd == "remove":
        try:
            reg.remove(args.name)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"removed {args.name}")
        return 0

    if cmd == "default":
        try:
            reg.set_default(args.name, model=args.model)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"default provider: {reg.default_provider} / {reg.default_model}")
        return 0

    if cmd == "test":
        name = args.name or reg.default_provider
        try:
            model = reg.build(name)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        health = model.health()
        print(f"[{'OK ' if health.healthy else 'BAD'}] {name} ({model.block_id}) {health.detail}")
        if args.generate:
            if not health.healthy:
                return 1
            answer = model.generate(RunContext(), "Reply with the single word: pong")
            print(f"generate -> {answer.text[:80]!r} (in/out tokens {answer.input_tokens}/{answer.output_tokens})")
        return 0 if health.healthy else 1

    return 2


def _cmd_health(args: argparse.Namespace) -> int:
    browser = HttpBrowser()
    model = _resolve_model(args.model)
    verifier = FileVerifier()

    rows = [
        (browser.block_id, browser.health().healthy, browser.health().detail),
        (f"model.{model.block_id}", model.health().healthy, model.health().detail),
        ("tool.file", FileTool().health().healthy, FileTool().health().detail),
        (verifier.block_id, True, f"verifies kind: {verifier.supports_kind}"),
    ]
    try:
        reg = _registry()
        rows.append(("providers.config", True, f"{len(reg.specs)} configured, default {reg.default_provider}"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("providers.config", False, str(exc)))
    width = max(len(name) for name, _, _ in rows)
    all_healthy = True
    for name, healthy, detail in rows:
        all_healthy &= healthy
        print(f"[{'OK ' if healthy else 'BAD'}] {name:<{width}}  {detail}")
    return 0 if all_healthy else 1


def _cmd_version(args: argparse.Namespace) -> int:
    from contracts import browser, memory, model, policy, secret_store, tool, verification

    print(f"ultron {__version__}")
    for mod in (model, browser, tool, verification, policy, memory, secret_store):
        print(f"  {mod.CONTRACT_ID} v{mod.CONTRACT_VERSION}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ultron",
        description="ULTRON personal AI runtime. No args = interactive TUI.",
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="visit a URL, summarize in one sentence, save to a file")
    p_run.add_argument("url", help="http(s) URL to visit")
    p_run.add_argument("-o", "--output", default=None, help="output file (default: ./summary.txt)")
    p_run.add_argument("--model", default=None, help="model name override (provider default otherwise)")
    p_run.add_argument("--timeout", type=float, default=30.0, help="browser timeout seconds")
    p_run.set_defaults(func=_cmd_run)

    p_ask = sub.add_parser("ask", help="one-shot question to the default provider")
    p_ask.add_argument("text", help="the question / prompt")
    p_ask.add_argument("--model", default=None, help="model name override")
    p_ask.add_argument("--system", default=None, help="optional system instruction")
    p_ask.add_argument("--no-memory", action="store_true", help="skip memory write/recall for this question")
    p_ask.set_defaults(func=_cmd_ask)

    p_chat = sub.add_parser("chat", help="multi-turn chat REPL with persistent memory")
    p_chat.add_argument("--model", default=None, help="model name override")
    p_chat.add_argument("--no-memory", action="store_true", help="disable persistent memory")
    p_chat.set_defaults(func=_cmd_chat)

    p_prov = sub.add_parser("providers", help="manage model providers")
    p_sub = p_prov.add_subparsers(dest="providers_cmd", required=True)

    p_sub.add_parser("list", help="list configured providers")

    p_add = p_sub.add_parser("add", help="add a provider")
    p_add.add_argument("name", help="short name, e.g. ollama-cloud, deepseek, groq")
    p_add.add_argument("kind", choices=KINDS, help="brick type")
    p_add.add_argument("--base-url", default="", help="API base URL (kind default if omitted)")
    p_add.add_argument("--model", default="", help="default model for this provider")
    p_add.add_argument("--api-key", default=None, help="API key (stored in the SecretStore, not the config)")
    p_add.add_argument("--set-default", action="store_true", help="make this the default provider")

    p_edit = p_sub.add_parser("edit", help="edit a provider")
    p_edit.add_argument("name")
    p_edit.add_argument("--kind", choices=KINDS, default=None)
    p_edit.add_argument("--base-url", default=None)
    p_edit.add_argument("--model", default=None, help="new default model (empty string clears)")
    p_edit.add_argument("--api-key", default=None, help="rotate the stored API key")

    p_rm = p_sub.add_parser("remove", help="remove a provider (and its secret)")
    p_rm.add_argument("name")

    p_def = p_sub.add_parser("default", help="set the default provider/model")
    p_def.add_argument("name")
    p_def.add_argument("--model", default=None, help="default model override")

    p_test = p_sub.add_parser("test", help="health-check (optionally generate) a provider")
    p_test.add_argument("name", nargs="?", default=None, help="provider name (default provider if omitted)")
    p_test.add_argument("--generate", action="store_true", help="also run a tiny generation round trip")
    p_prov.set_defaults(func=_cmd_providers)

    p_health = sub.add_parser("health", help="report block health")
    p_health.add_argument("--model", default=None, help="model name to check")
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
