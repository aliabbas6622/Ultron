# ULTRON AI Coder Pack — V2

This pack is the implementation brief for an AI coding agent building **ULTRON**, a modular personal AI runtime.

ULTRON is designed as a collection of independently replaceable blocks. The long-term system may span multiple explicitly paired devices, but **V0.x is single-node first**.

## Read Order

1. `01_VISION.md`
2. `02_ARCHITECTURE.md`
3. `03_BLOCK_CONTRACT.md`
4. `04_TECH_STACK.md`
5. `05_AGENT_INSTRUCTIONS.md`
6. `06_MEMORY_CONTEXT_ROUTING.md`
7. `07_SECURITY_AND_CONTAINMENT.md`
8. `08_ACCEPTANCE_AND_ROADMAP.md`
9. `09_CODING_STANDARDS.md`

## Constitution

- Contracts, not vendors.
- Behaviorally swappable blocks, not cosmetic adapters.
- Cold-swap support is mandatory for every block.
- Hot-swap is optional and must be declared.
- No autonomous host acquisition or self-installation.
- Hosting authority and instruction authority are separate.
- The LLM never authorizes its own privileged actions.
- State beats replaying chat history.
- Context is compiled, not concatenated.
- Large data stays outside model context unless selected.
- Every expensive or side-effecting action is observable and budgeted.
- "Done" is verified where verification is possible.
- V0.1 must deliver one working vertical slice before expansion.
