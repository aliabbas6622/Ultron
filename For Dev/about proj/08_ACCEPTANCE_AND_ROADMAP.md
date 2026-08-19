# Acceptance and Roadmap

## V0.1 Definition of Done

Scenario:

> "Visit example.com, determine in one sentence what the page is for, and save that sentence to summary.txt."

Pass only if:

```text
[ ] one end-to-end run succeeds
[ ] no vendor implementation is imported by core
[ ] cancellation exists through the run path
[ ] browser output is projected, not raw-dumped to model
[ ] model sees only selected tool capability/schema
[ ] file write becomes ActionIntent
[ ] policy evaluates the write
[ ] filesystem result is verified
[ ] state records the side effect
[ ] trace/run ID exists
[ ] token usage is recorded
[ ] tool/model errors are normalized
[ ] browser failure does not crash runtime
```

Do not expand scope until this passes.

## Eval Harness — V0.1

Build alongside the runtime.

Support:

### Deterministic fixtures
- expected route
- expected policy
- expected capability selection
- expected side effects

### Replay fixtures
- frozen model/tool responses
- run infrastructure changes without paying model cost

### Quality/efficiency metrics
- success
- verification result
- input/output tokens
- cached tokens
- latency
- retries
- tool calls
- cost
- context breakdown

A routing/context optimization is not accepted solely because it uses fewer tokens.

## V0.2
Persistent structured memory + migrations.

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
