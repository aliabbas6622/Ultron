# Instructions for the AI Coding Agent

## Primary Goal

Build one working vertical slice before broad architecture expansion.

Do not create dozens of empty modules.

## V0.1 First Slice

Target behavior:

> User asks: "Visit example.com, determine in one sentence what the page is for, and save that sentence to summary.txt."

Required path:

```text
user input
-> runtime
-> task/model routing
-> browser
-> page projection
-> context compiler
-> model
-> ActionIntent(write file)
-> policy
-> filesystem tool
-> verification
-> state update
-> response
```

No other major feature takes priority until this path passes its acceptance tests.

## Implementation Order

1. core IDs/errors/run context
2. cancellation/deadline support
3. minimal event/runtime path
4. block contracts
5. one model adapter
6. one browser adapter
7. minimal context compiler
8. ActionIntent + policy
9. file tool
10. verifier
11. telemetry
12. acceptance test
13. only then persistence/memory expansion

## No Vendor Logic in Core

Vendor imports belong in adapters.

Add a CI/dependency-direction test preventing vendor modules from being imported by core packages.

## Prompt Management

Do not scatter prompt strings through code.

Use versioned instruction bundles:

```text
id
version
content_hash
role
cache_class
token_count
```

Every run should record the instruction versions used.

## Side Effects

Model output never directly causes privileged effects.

Always:

```text
Model
-> ActionIntent
-> PolicyEngine
-> ToolExecutor
```

## Verification

Where possible, verify outcomes using external evidence.

"Done" from the model is not proof.

## Dependencies

For every dependency document:
- why it exists
- which contract contains it
- failure behavior
- replacement path
- license implications
