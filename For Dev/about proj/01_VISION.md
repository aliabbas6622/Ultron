# Vision

## What ULTRON Is

ULTRON is a personal AI runtime whose capabilities behave like LEGO blocks.

A model, browser, memory backend, tool service, device, or transport can be replaced without redefining ULTRON.

Long term, ULTRON may exist at different densities across multiple explicitly paired devices. A phone may contribute camera/GPS/microphone capabilities while a workstation contributes GPU/browser/filesystem capabilities.

ULTRON is the logical organism formed by:
- identity
- contracts
- state
- policy
- routing
- memory
- context compilation
- capability discovery

No single vendor or device is the organism.

## Long-Term Shape

```text
          ULTRON FABRIC

   Node A <-----> Node B
     |              |
 capabilities    capabilities
     \              /
      \            /
       shared contracts
       shared identity
       shared policy
```

This is an end-state architectural direction, **not a V0.1 implementation requirement**.

## Hard Non-Goals

ULTRON is not:
- a self-propagating agent
- a system that installs or activates itself on infrastructure without explicit per-device human action
- a giant agent-framework fork
- a prompt collection
- a single-vendor assistant
- an unrestricted autonomous shell
- a distributed system before the single-node runtime works

## Core Principles

1. State over history.
2. Retrieval over replay.
3. Filtering over summarization.
4. References over raw bulk data.
5. Lazy schema loading over tool-schema dumping.
6. Deterministic code over LLM calls when possible.
7. Cache over recomputation.
8. Typed internal structures over prose.
9. Format selection over format dogma.
10. Model-independent memory.
11. Policy outside the model.
12. Verification over trusting completion claims.
13. Graceful degradation over global failure.
14. Measure tokens, latency, cost, retries, and side effects.
15. Nothing enters context without justification.
