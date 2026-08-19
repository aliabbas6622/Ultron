# Coding Standards

## Priorities

1. correctness
2. contract clarity
3. graceful degradation
4. observability
5. efficiency
6. performance

## Contract-First

Before integrating a vendor, define or extend a versioned block contract.

Every implementation must pass the corresponding conformance tests.

## Typed Data

Prefer typed internal structures.

Avoid arbitrary map/dictionary payloads when a stable schema exists.

## Serialization

Use serialization only at boundaries.

```text
internal -> typed structures
subprocess -> framed protocol
MCP -> protocol-required JSON-RPC
external API -> required format
LLM-facing -> benchmarked compact representation
```

## Token Discipline

Before model context:
- filter
- project
- dedupe
- prune schemas
- reference large artifacts
- count tokens
- enforce run/context budget

## Streaming and Cancellation

Do not implement model/tool interfaces that cannot be cancelled.

Streaming may be optional per block capability, but cancellation is part of the minimum model/runtime contract.

## Logging

Structured logs only.

Include:
- run ID
- block ID/version
- route decision
- timing
- budget usage
- failure class

Never log raw secrets.

## Migrations

Database schemas and semantic memory formats must be versioned.

No destructive schema edits without migrations.

## Tests

Required categories:

```text
vertical-slice acceptance
block conformance
browser unavailable
memory unavailable
provider unavailable
rate limit
timeout/cancellation
malformed tool result
duplicate side-effect retry
verification failure
prompt injection
MCP description poisoning
dependency-direction violation
```

## Avoid

- god objects
- giant empty directory scaffolds
- circular dependencies
- vendor checks in core
- unbounded retries
- prompt strings embedded everywhere
- raw tool output automatically entering context
- permissions defined only in prompts
- claiming swappability without conformance tests
