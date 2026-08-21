# Architecture

## Single-Node V0.x

```text
User
  |
ULTRON Identity
  |
Runtime / Event System
  |
  +--> Task Router
  +--> Context Compiler
  +--> Model Block
  +--> Memory Block
  +--> Policy Engine
  +--> Tool Bus
          |
          +--> Browser Block
          +--> File Block
          +--> MCP Blocks
```

## Block Rule

The core depends on versioned contracts.

Wrong:

```text
runtime -> Lightpanda internals
runtime -> PostgreSQL-specific behavior
runtime -> DeepSeek-specific fields
```

Correct:

```text
runtime -> BrowserContract
runtime -> MemoryContract
runtime -> ModelContract
```

Concrete implementations live behind blocks.

Every brick is bidirectionally portable: contracts/ is self-contained (stdlib
only) and tools/ depends on contracts/ only, so any external agent can adopt
ULTRON bricks — or provide bricks ULTRON composes — with `check_alive()` as
the single host-side requirement. The runtime owns no block implementation;
composition happens at the edges (TUI, tests, future hosts).

## Runtime Concepts

Every run should carry:

```text
RunContext
- run_id
- trace_id
- deadline
- cancellation token
- global budget
- user/owner identity
- trust context
```

Cancellation and deadlines are architectural primitives, not later UI features.

## Events

Use typed events such as:

```text
UserInput
ModelStarted
ModelDelta
ModelCompleted
ToolRequested
ToolCompleted
PolicyDenied
VerificationFailed
ComponentDegraded
ComponentRecovered
RunCancelled
```

Do not build a giant synchronous loop that owns all behavior.

## Failure Domains

Use three integration levels:

1. Native module
2. Local subprocess
3. HTTP/MCP service

A failing optional block should degrade capability, not terminate ULTRON.

## Identity

`UltronIdentity` is not a personality prompt.

It should eventually include:

```text
instance_id
owner_id
name
behavior_profile_ref
policy_profile_ref
device_identity
trusted_environment
capabilities
```

Future distributed identity may add cryptographic node identity, but V0.x does not need multi-device fabric implementation.
