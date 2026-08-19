# Memory, Context, and Routing

## Memory

Do not treat conversation replay as memory.

Initial memory classes:

```text
identity
preferences
semantic
episodic
procedural
relationship
environmental
```

Execution/task state remains separate.

## Memory Record

Conceptual fields:

```text
id
kind
subject
predicate
value
source
observed_at
valid_from
valid_until
confidence
importance
sensitivity
scope
supersedes
contradictions
content_hash
```

Database migrations are required from the first schema version.

## Context Compiler

Inputs:
- task
- current state
- model profile
- relevant memory
- selected tools
- observations
- trust labels
- artifact references
- token budget

Passes:

```text
select
filter
dedupe
project
prune tools
preserve trust/provenance
choose representation
count tokens
fit budget
arrange cacheable/dynamic regions
validate
```

Large outputs remain external as artifact references.

## Runtime Budgets

Each run should support ceilings such as:

```text
max_wall_time
max_input_tokens
max_output_tokens
max_model_calls
max_tool_calls
max_workers
max_money
max_external_writes
deadline
```

Workers inherit sub-budgets.

## Routing Levels

```text
L0 deterministic code
L1 tiny/local model
L2 standard model
L3 reasoning model
L4 specialist/worker orchestration
```

Choose the cheapest level that can reliably complete the task.

## Router Inputs

Use:
- required modalities
- task difficulty
- privacy
- latency target
- token estimate
- reliability target
- provider health
- hardware load
- cache availability
- cost ceiling
- block capabilities

Record predicted vs observed performance for future learned routing.

## Tool Schemas

Do not expose all full schemas every turn.

Use:
- compact capability catalog
- lazy exact-schema loading
- capability negotiation
