# Block Contract

## Principle

A block is not swappable merely because it has the same method names.

A replacement is valid only when it satisfies the same **behavioral contract**.

## Brick Independence (LEGO Rule)

Every part is usable OUTSIDE ULTRON, and every part is replaceable from outside:

- `contracts/` is **self-contained**: stdlib + contracts only (enforced by
  tests/test_dependency_direction.py). It is the entire interchange spec —
  any agent out there can implement or consume it with no ULTRON code.
- `tools/` is **grab-and-go**: copy `contracts/ + tools/` into any host and
  drive file_write intents with anything that has `check_alive()`. It never
  imports core/.
- `core/` is **pure composition**: the runtime owns no block implementation.
  Browser, model, policy, tool executor, and verifier are all injected
  parameters behind contract protocols — swap any brick at call time.
- `contracts/call_context.py` `CallContext` is the whole host-side
  requirement: cooperative cancellation via `check_alive()`. ULTRON's
  RunContext satisfies it structurally; so does a 3-line class in any other
  framework. Blocks never import a runtime context type.
- `contracts/action.py` `ActionIntent` is the interchange format both ways:
  external agents can emit intents our tools execute; our agents can emit
  intents external executors run.
- All Protocols are `@runtime_checkable` so a host can do bind-time
  conformance checks (capability negotiation, below).

Every block must define:

```text
contract_id
contract_version
minimum behavior
optional capabilities
failure semantics
health semantics
state ownership
migration requirements
cold_swap support
hot_swap declaration
conformance suite
```

## Minimal Contract + Optional Capabilities

Do not force all implementations to the lowest common denominator.

Example:

```text
ModelProvider v1

required:
- text_generation
- cancellation
- health
- usage_reporting

optional:
- streaming
- vision
- audio
- tool_calling
- embeddings
- prompt_cache
- structured_output
```

Routers must query declared capabilities rather than assume them.

## Contracts Shipped (contracts/)

```text
call_context.py  CallContext        what a host must provide a brick: check_alive()
action.py        ActionIntent       agent -> tool interchange format (id = idempotency key)
errors.py        error taxonomy     normalized failure semantics for every brick
health.py        HealthStatus       shared health reporting type
model.py         ModelProvider v1   generate / health / capabilities
browser.py       BrowserProvider v1 visit / health, PageProjection out
memory.py        MemoryProvider v1  write / retrieve / health, MemoryRecord with provenance
tool.py          ToolProvider v1    describe (lazy schemas) / execute / health, ToolResult
verification.py  Verifier v1        verify(intent, result) against external evidence
policy.py        PolicyEvaluator v1 evaluate(intent) -> ALLOW/DENY (ASK/CONSTRAIN/SANDBOX reserved)
```

ToolProvider v1 contract in brief:

- `describe()` returns the compact catalog; `params_schema` stays `None`
  until a host asks (lazy schema loading — a big registry never bloats context).
- `execute()` receives only policy-approved intents; executing a kind the
  tool never advertised raises `ContractViolationError`.
- Failed executions return `ToolResult(ok=False, error=...)` — normalized,
  never a raw vendor exception; the runtime maps that to `ToolExecutionError`.
- `action_id` is the idempotency key: re-executing the same intent must leave
  the same final state (duplicate side-effect retry safety).

## Conformance Suites

Each contract must ship tests that every implementation must pass.

Examples:

### Memory
- preserves provenance
- preserves confidence
- respects supersedes/contradictions semantics
- deterministic conflict behavior
- retrieval does not drop required fields

### Browser
- navigation
- extraction
- timeout behavior
- cancellation
- invalid URL handling
- health reporting

### Model
- cancellation
- timeout
- usage reporting
- normalized errors
- capability advertisement

### Tool
- executes only advertised kinds (ContractViolationError otherwise)
- failed executions normalize to ToolResult(ok=False)
- idempotent retry on the same action_id
- lazy schema loading (describe() never embeds full schemas by default)

### Verifier
- outcome is evidence-based, never trusts ToolResult.ok
- catches tampered/lying tool output (content mismatch)

A new block is not production-compatible until it passes its contract suite.

## Cold Swap

Mandatory for all blocks.

Meaning:
- stop using old implementation
- configure replacement
- restart/rebind
- system remains behaviorally valid

## Hot Swap

Optional.

A block declaring hot-swap must specify:

```text
drain_required
inflight_request_behavior
state_transfer
dual_write_required
rollback_behavior
```

Do not promise hot-swap universally in V0.x.

## Capability Negotiation

At bind/connect time, blocks advertise capabilities.

The system records them in the registry and routes only compatible work.

## Dependency Direction

Core may import contract definitions.

Core must not import vendor implementations.

CI should enforce this mechanically where possible.

## Mojo Adapter Bridge

Core is Python. Mojo runs only inside WSL2 (no native Windows host run), so
core cannot import a Mojo block in-process on any platform where the host is
Windows. The bridge is a subprocess, not a language question.

Contract: each Mojo block ships a CLI entrypoint that does exactly one
request/response round trip over stdio, then exits.

```text
stdin  (one line): {"method": "<method_name>", ...method args}
stdout (one line): {"ok": true, ...result fields}
              or:  {"ok": false, "error": "<code>", "detail": "<string>"}
```

`error` codes map onto `core.errors`: `cancelled`, `deadline_exceeded`,
`block_unavailable`, `contract_violation`. Anything else normalizes to
`block_unavailable`.

Method shapes (ModelProvider v1 / BrowserProvider v1):

```text
generate  in:  {"method": "generate", "prompt": str, "deadline_at": float|null}
          out: {"ok": true, "text": str, "input_tokens": int, "output_tokens": int, "finish_reason": str}

visit     in:  {"method": "visit", "url": str, "timeout_s": float}
          out: {"ok": true, "url": str, "title": str, "text": str, "truncated": bool}

health    in:  {"method": "health"}
          out: {"ok": true, "healthy": bool, "detail": str}
```

`core/mojo_bridge.py` is the generic, block-agnostic transport (spawns the
WSL process, enforces the timeout, parses/normalizes the response). It is
core-owned infrastructure, not a vendor implementation. A thin per-block
Python wrapper that calls it and returns `ModelProvider`/`BrowserProvider`
dataclasses is still required to actually satisfy those Protocols, and that
wrapper lives with the adapter, not in core.
