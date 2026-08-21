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
WORKSPACE_DIR = os.path.join(os.environ.get("ULTRON_HOME", os.path.join(os.path.expanduser("~"), ".ultron")), "workspace")


def _resolve_model(model_name: str | None = None):
    return select_model(model_name)


def _agent_tools(no_tools: bool):
    """The chat agent's tool belt: policy-gated file writes (contained to the
    workspace) + read-only web fetch."""
    from tools.file_tool import FileTool
    from tools.web_fetch import WebFetchTool

    return [] if no_tools else [FileTool(base_dir=WORKSPACE_DIR), WebFetchTool()]


def _agent_policy(no_tools: bool):
    from core.policy import PolicyEngine

    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    return PolicyEngine(allowed_write_dir=WORKSPACE_DIR)


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


def _skill_system(skill_flag: str | None, base_system: str | None) -> str | None:
    """Fold --skill names into the agent system prompt."""
    from skills.store import FileSkillStore, skill_system_section

    if not skill_flag:
        return base_system
    store = FileSkillStore()
    loaded = [store.load(name.strip()) for name in skill_flag.split(",") if name.strip()]
    section = skill_system_section(loaded)
    return "\n\n".join(part for part in (base_system, section) if part)


def _cmd_ask(args: argparse.Namespace) -> int:
    from core.agent import agent_turn

    run = RunContext()
    model = _resolve_model(args.model)
    memory = _open_memory() if not args.no_memory else None
    try:
        outcome = agent_turn(
            run, model, [], args.text,
            policy=_agent_policy(args.no_tools), tools=_agent_tools(args.no_tools),
            verifiers=_agent_verifiers(args.no_tools), memory=memory,
            system=_skill_system(getattr(args, "skill", None), args.system),
        )
    except UltronError as exc:
        print(f"FAILED [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 1
    finally:
        if memory is not None:
            memory.close()
    print(outcome.answer.text)
    for denial in outcome.denials:
        print(f"[denied] {denial}", file=sys.stderr)
    return 0


def _agent_verifiers(no_tools: bool):
    from tools.file_verifier import FileVerifier

    return [] if no_tools else [FileVerifier()]


def _cmd_chat(args: argparse.Namespace) -> int:
    from core.agent import agent_turn
    from core.chat import ChatMessage

    registry = ProviderRegistry.load()
    model = _resolve_model(args.model)
    memory = _open_memory() if not args.no_memory else None
    tools = _agent_tools(args.no_tools)
    policy = _agent_policy(args.no_tools)
    verifiers = _agent_verifiers(args.no_tools)
    system = _skill_system(getattr(args, "skill", None), None)
    history: list[ChatMessage] = []
    run = RunContext()
    who = f"{model.block_id}"
    if registry.default_provider:
        who += f" (default: {registry.default_provider}"
        if registry.default_model:
            who += f"/{registry.default_model}"
        who += ")"
    mode = "agent (tools: " + ", ".join(sorted({d.kind for t in tools for d in t.describe()})) + ")" if tools else "chat"
    print(f"ULTRON {mode} — {who}. /exit /clear /memory /tools. Writes land in {WORKSPACE_DIR}")
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
        if text == "/tools":
            for tool in tools:
                for d in tool.describe():
                    print(f"  {d.kind}: {d.description} [{d.risk_class}]")
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
            outcome = agent_turn(run, model, history, text, policy=policy, tools=tools,
                                 verifiers=verifiers, memory=memory, system=system)
        except UltronError as exc:
            print(f"[{type(exc).__name__}] {exc}")
            continue
        history.append(ChatMessage(role="user", text=text))
        history.append(outcome.answer)
        for denial in outcome.denials:
            print(f"  [denied] {denial}")
        for intent, result in zip(outcome.actions, outcome.results):
            mark = "+" if result.ok else "x"
            print(f"  [{mark}] {intent.kind} -> {result.outputs.get('path') or result.outputs.get('url') or ''}")
        print(f"ultron> {outcome.answer.text}")
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


def _cmd_route(args: argparse.Namespace) -> int:
    from core.router import CandidateSpec, ModelRouter, RouteRequest

    registry = ProviderRegistry.load()
    tier = {"ollama": 1, "ollama-cloud": 2, "openai-compatible": 2}
    candidates = [
        CandidateSpec(name="stub", kind="stub", cost_tier=0, local=True, healthy=True, model="extractive")
    ]
    for name, spec in registry.specs.items():
        healthy = spec.name == registry.default_provider  # only ping the default (cheap); others assumed up
        candidates.append(CandidateSpec(name=name, kind=spec.kind, cost_tier=tier.get(spec.kind, 2),
                                        local=spec.kind == "ollama", healthy=healthy, model=spec.model))
    decision = ModelRouter(candidates).route(RouteRequest(
        task_text=args.text, privacy_required=args.privacy, tool_use_needed=args.tools,
        difficulty=args.difficulty, latency_target_s=args.latency,
    ))
    print(f"level     {decision.level}")
    print(f"provider  {decision.provider_name or '(none)'}")
    print(f"reason    {decision.reason}")
    if decision.fallback_chain:
        print(f"fallback  {' -> '.join(decision.fallback_chain)}")
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    import time as _time

    from core.scheduler import JobStore, Scheduler, schedules_path

    store = JobStore(schedules_path())
    sched = Scheduler(store)
    cmd = args.schedule_cmd

    if cmd == "list":
        jobs = sorted(store.load().values(), key=lambda j: j.next_run)
        if not jobs:
            print(f"no jobs (config: {store.path})")
            return 0
        print(f"{'NAME':<20} {'EVERY':>8} {'NEXT RUN':>12} {'ENABLED':>8}  LAST RESULT")
        for job in jobs:
            eta = f"in {max(0.0, job.next_run - _time.time()):.0f}s"
            print(f"{job.name:<20} {job.every_s:>7.0f}s {eta:>12} {str(job.enabled):>8}  {job.last_result[:60]}")
        return 0

    if cmd == "add":
        job = sched.add_job(args.name, args.prompt, args.every)
        print(f"scheduled {job.name}: every {job.every_s:.0f}s")
        return 0

    if cmd == "remove":
        jobs = store.load()
        if args.name not in jobs:
            print(f"error: unknown job {args.name!r}", file=sys.stderr)
            return 2
        store.remove(args.name)
        print(f"removed {args.name}")
        return 0

    if cmd == "tick":
        from core.agent import agent_turn

        def runner(job):
            run = RunContext()
            memory = _open_memory()
            try:
                outcome = agent_turn(run, _resolve_model(None), [], job.prompt,
                                     policy=_agent_policy(False), tools=_agent_tools(False),
                                     verifiers=_agent_verifiers(False), memory=memory)
                return outcome.answer.text
            finally:
                if memory is not None:
                    memory.close()

        ran = sched.run_due(runner)
        if not ran:
            print("nothing due")
        for job, error in ran:
            status = "ok" if error is None else f"FAILED ({error})"
            print(f"[{job.name}] {status}: {job.last_result[:120]}")
        return 0 if all(err is None for _, err in ran) else 1

    return 2


def _cmd_plan(args: argparse.Namespace) -> int:
    from core.plans import Plan, PlanStep, PlanStore, plans_dir

    store = PlanStore(plans_dir())
    cmd = args.plan_cmd

    if cmd == "new":
        import time as _time

        if not args.step:
            print("error: provide at least one --step", file=sys.stderr)
            return 2
        plan = Plan(name=args.name, goal=args.goal,
                    steps=[PlanStep(description=s) for s in args.step], created_at=_time.time())
        store.save(plan)
        print(f"plan {plan.name}: {len(plan.steps)} steps toward: {plan.goal}")
        return 0

    if cmd == "list":
        plans = store.list()
        if not plans:
            print("no plans")
            return 0
        for p in plans:
            done, total = p.progress()
            print(f"{p.name:<20} {p.status:<12} {done}/{total} steps  {p.goal[:60]}")
        return 0

    if cmd == "show":
        p = store.load(args.name)
        print(f"plan {p.name} [{p.status}] — {p.goal}")
        for i, step in enumerate(p.steps):
            mark = {"done": "+", "in_progress": ">", "failed": "x", "pending": " "}.get(step.status, "?")
            print(f"  [{mark}] {i}: {step.description}  ({step.status})")
        return 0

    if cmd == "step":
        import time as _time

        p = store.set_step_status(args.name, args.index, args.status, now=_time.time())
        step = p.steps[args.index]
        nxt = p.next_step()
        print(f"step {args.index} -> {args.status}; next: {nxt.description if nxt else '(plan complete)'}")
        return 0

    if cmd == "next":
        p = store.load(args.name)
        nxt = p.next_step()
        print(nxt.description if nxt else "(plan complete)")
        return 0

    if cmd == "delete":
        store.delete(args.name)
        print(f"deleted {args.name}")
        return 0

    return 2


def _cmd_skills(args: argparse.Namespace) -> int:
    from skills.store import FileSkillStore, Skill

    store = FileSkillStore()
    cmd = args.skills_cmd

    if cmd == "list":
        skills = store.list()
        if not skills:
            print(f"no skills (create one: ultron skills create <name> --description ... --instructions ...)")
            return 0
        for s in skills:
            tools = ",".join(s.tools) or "-"
            print(f"{s.name:<20} v{s.version:<6} [{tools}] {s.description[:70]}")
        print("\nuse with: ultron ask/chat --skill <name>")
        return 0

    if cmd == "show":
        s = store.load(args.name)
        print(f"{s.record_id()}\n{s.description}\n\n{s.instructions}")
        return 0

    if cmd == "create":
        s = Skill(name=args.name, description=args.description, instructions=args.instructions,
                  tools=tuple(t.strip() for t in (args.tools or "").split(",") if t.strip()),
                  version=args.version)
        store.save(s)
        print(f"saved {s.record_id()} ({store.dir / (s.name + '.toml') if hasattr(store, 'dir') else ''})")
        return 0

    if cmd == "delete":
        store.delete(args.name)
        print(f"deleted {args.name}")
        return 0

    return 2


def _cmd_fanout(args: argparse.Namespace) -> int:
    from core.workers import WorkerPool, WorkerTask, pool_totals

    run = RunContext()
    model = _resolve_model(args.model)
    tasks = [WorkerTask(role=f"worker-{i + 1}", prompt=p) for i, p in enumerate(args.task)]
    print(f"spawning {len(tasks)} workers (parallelism {args.parallel}) on {model.block_id}", file=sys.stderr)
    results = WorkerPool(parent=run, max_parallel=args.parallel).run_all(model, tasks)
    for r in results:
        if r.error_class:
            print(f"[{r.worker_id}/{r.role}] FAILED {r.error_class}")
        else:
            print(f"[{r.worker_id}/{r.role}]\n{r.text}\n")
    tin, tout = pool_totals(results)
    print(f"workers: {len(results)} | tokens in/out: {tin}/{tout}", file=sys.stderr)
    return 0 if all(r.error_class is None for r in results) else 1


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

    p_ask = sub.add_parser("ask", help="one-shot question (tools enabled) to the default provider")
    p_ask.add_argument("text", help="the question / prompt")
    p_ask.add_argument("--model", default=None, help="model name override")
    p_ask.add_argument("--system", default=None, help="optional system instruction")
    p_ask.add_argument("--skill", default=None, help="skill name(s), comma-separated (ultron skills list)")
    p_ask.add_argument("--no-memory", action="store_true", help="skip memory write/recall for this question")
    p_ask.add_argument("--no-tools", action="store_true", help="plain chat, no tool loop")
    p_ask.set_defaults(func=_cmd_ask)

    p_chat = sub.add_parser("chat", help="agentic multi-turn REPL: tools + persistent memory")
    p_chat.add_argument("--model", default=None, help="model name override")
    p_chat.add_argument("--skill", default=None, help="skill name(s), comma-separated, injected every turn")
    p_chat.add_argument("--no-memory", action="store_true", help="disable persistent memory")
    p_chat.add_argument("--no-tools", action="store_true", help="plain chat, no tool loop")
    p_chat.set_defaults(func=_cmd_chat)

    p_route = sub.add_parser("route", help="preview the adaptive model-routing decision for a task")
    p_route.add_argument("text", help="the task text")
    p_route.add_argument("--privacy", action="store_true", help="task requires local-only models")
    p_route.add_argument("--tools", action="store_true", help="task needs tool calling")
    p_route.add_argument("--difficulty", default="normal", choices=["trivial", "normal", "hard"])
    p_route.add_argument("--latency", type=float, default=None, help="latency target seconds")
    p_route.set_defaults(func=_cmd_route)

    p_sched = sub.add_parser("schedule", help="recurring background jobs (autonomy)")
    sched_sub = p_sched.add_subparsers(dest="schedule_cmd", required=True)
    sched_sub.add_parser("list", help="list jobs and next runs")
    p_add = sched_sub.add_parser("add", help="add a recurring job")
    p_add.add_argument("name", help="job slug")
    p_add.add_argument("--every", type=float, required=True, help="seconds between runs")
    p_add.add_argument("--prompt", required=True, help="what the agent does each run")
    p_rm = sched_sub.add_parser("remove", help="remove a job")
    p_rm.add_argument("name")
    sched_sub.add_parser("tick", help="run all due jobs now (call from a timer/cron)")
    p_sched.set_defaults(func=_cmd_schedule)

    p_plan = sub.add_parser("plan", help="long-term persistent, resumable plans")
    plan_sub = p_plan.add_subparsers(dest="plan_cmd", required=True)
    p_new = plan_sub.add_parser("new", help="create a plan")
    p_new.add_argument("name", help="plan slug")
    p_new.add_argument("--goal", required=True)
    p_new.add_argument("--step", action="append", required=True, help="step description (repeatable)")
    plan_sub.add_parser("list", help="list plans with progress")
    p_show = plan_sub.add_parser("show", help="show a plan's steps")
    p_show.add_argument("name")
    p_step = plan_sub.add_parser("step", help="update a step's status")
    p_step.add_argument("name")
    p_step.add_argument("index", type=int)
    p_step.add_argument("status", choices=["pending", "in_progress", "done", "failed"])
    p_next = plan_sub.add_parser("next", help="print the next resumable step")
    p_next.add_argument("name")
    p_pdel = plan_sub.add_parser("delete", help="delete a plan")
    p_pdel.add_argument("name")
    p_plan.set_defaults(func=_cmd_plan)

    p_skills = sub.add_parser("skills", help="reusable skill bundles")
    skills_sub = p_skills.add_subparsers(dest="skills_cmd", required=True)
    skills_sub.add_parser("list", help="list skills")
    p_show_s = skills_sub.add_parser("show", help="show one skill")
    p_show_s.add_argument("name")
    p_mk = skills_sub.add_parser("create", help="create/update a skill")
    p_mk.add_argument("name")
    p_mk.add_argument("--description", required=True)
    p_mk.add_argument("--instructions", required=True)
    p_mk.add_argument("--tools", default=None, help="comma-separated tool kinds it uses")
    p_mk.add_argument("--version", default="1.0.0")
    p_sdel = skills_sub.add_parser("delete", help="delete a skill")
    p_sdel.add_argument("name")
    p_skills.set_defaults(func=_cmd_skills)

    p_fan = sub.add_parser("fanout", help="multi-agent workers: run prompts in parallel specialist sub-agents")
    p_fan.add_argument("--task", action="append", required=True, help="worker prompt (repeatable)")
    p_fan.add_argument("--model", default=None, help="model name override")
    p_fan.add_argument("--parallel", type=int, default=2, help="max concurrent workers")
    p_fan.set_defaults(func=_cmd_fanout)

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
