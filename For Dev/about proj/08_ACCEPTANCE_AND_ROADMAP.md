# Acceptance and Roadmap

## V0.1 Definition of Done

Scenario:

> "Visit example.com, determine in one sentence what the page is for, and save that sentence to summary.txt."

Pass only if:

```text
[x] one end-to-end run succeeds                     (tests/test_acceptance_slice.py, tui drives real blocks)
[x] no vendor implementation is imported by core    (tests/test_dependency_direction.py)
[x] cancellation exists through the run path        (test_acceptance_slice.py::test_cancellation_stops_...)
[x] browser output is projected, not raw-dumped     (PageProjection contract + tests/test_context_compiler.py)
[x] model sees only selected tool capability/schema (compiled prompt is frame-only, frozen by replay hash)
[x] file write becomes ActionIntent                 (core/runtime.py, test_policy_denies_* proves the path)
[x] policy evaluates the write                      (test_policy_denies_write_outside_allowed_dir)
[x] filesystem result is verified                   (test_failure_modes.py::test_verification_failure_...)
[x] state records the side effect                   (budget counters + typed events asserted in acceptance)
[x] trace/run ID exists                             (RunContext run_id/trace_id asserted)
[x] token usage is recorded                         (budget.used_input/output_tokens asserted)
[x] tool/model errors are normalized                (provider-unavailable + browser-failure tests)
[x] browser failure does not crash runtime          (test_browser_failure_does_not_crash_runtime)
```

STATUS 2026-08-22: all items green against fake/replay blocks; the TUI also
drives the slice with real stand-in blocks (HttpBrowser + OllamaModel when a
local ollama is up). Real Mojo adapters still need the CLI entrypoints flagged
in TEAM_SPLIT.txt.

Do not expand scope until this passes.

## Eval Harness — V0.1 — BUILT (core/eval_harness.py, tests/test_eval_harness.py)

Support:

### Deterministic fixtures
- expected route ✓ (Scenario + run_scenario route grading)
- expected policy ✓ (expected_policy allow/deny)
- expected capability selection ✓ (replay blocks advertise the frozen capability set)
- expected side effects ✓ (expected_file_content + no-write-on-failure grading)

### Replay fixtures
- frozen model/tool responses ✓ (ReplayModel/ReplayBrowser)
- run infrastructure changes without paying model cost ✓ (freeze() pins the
  compiled-prompt hash; any infra change that alters the prompt fails replay)

### Quality/efficiency metrics — RunMetrics (core/telemetry.py)
- success ✓
- verification result ✓
- input/output tokens ✓
- cached tokens ✓ (ModelResult.cached_tokens)
- latency ✓
- retries ✓ (0 until a retry loop exists; adapters must report into it)
- tool calls ✓
- cost ✓ (budget.used_money_usd)
- context breakdown ✓ (per-section token estimates in CompiledContext.breakdown)

A routing/context optimization is not accepted solely because it uses fewer tokens.

## V0.2 — IN PROGRESS
Persistent structured memory + migrations.

STATUS 2026-08-22: MemoryProvider v1 contract (contracts/memory.py) with the
full 06 record schema; SQLite dev backend with day-one migrations
(core/memory_sqlite.py, schema_migrations table, append-only MIGRATIONS list);
conformance suite tests/test_memory_conformance.py covers provenance,
confidence, supersedes/contradictions, deterministic conflict behavior, no
dropped fields, reopen persistence. REMAINS: PostgreSQL+pgvector default
backend, wiring memory retrieval into the context compiler's "relevant memory"
input (that wiring is V0.3 context-compiler work per 06).

## V0.3
Context compiler optimization, artifact references, schema pruning, optional TOON benchmarking.

## V0.4
Browser routing between Chromium correctness path and Lightpanda fast path.

## V0.5
Voice block behind a stable contract.

## V0.6
Disposable workers with strict sub-budgets and isolated context.

## Later
- distributed node fabric
- cryptographic pairing
- capability materialization across explicitly paired devices
- hot-swap of selected blocks
- vision/desktop
- scheduling/autonomy
- smart-home
- mobile ecosystem

The end-state vision must not force distributed complexity into early milestones.
