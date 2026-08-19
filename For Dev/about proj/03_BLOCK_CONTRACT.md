# Block Contract

## Principle

A block is not swappable merely because it has the same method names.

A replacement is valid only when it satisfies the same **behavioral contract**.

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
