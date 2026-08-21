# Capability Roadmap — Ultron-like Features

Status vocabulary: **BUILT** (works today, tested), **SCAFFOLDED** (contracts +
engines in place, integration pending), **DEFERRED** (needs hardware/deps/
design, tracked here so it is not lost).

Constitution still applies: every capability is a block behind a versioned
contract; nothing expands past the policy boundary; distributed features obey
the 07 containment invariant (no self-install, ever).

## BUILT (single-node)

- **Adaptive model routing** — core/router.py: L0-L4 levels per 06 (privacy,
  difficulty, tool-use, latency); `ultron route` previews decisions.
- **Tool creation / skills** — skills/ package (self-contained), TOML skill
  bundles with version+content-hash; `ultron skills` CRUD; `--skill` injects
  into agent system prompts.
- **Long-term planning** — core/plans.py: persistent resumable plans with
  per-step checkpoints; `ultron plan` CRUD + `next`.
- **Scheduling / autonomy** — core/scheduler.py: recurring jobs with
  exception-isolated runs; `ultron schedule` (list/add/remove/tick); tick
  drives the full agentic loop (tools, verification, memory).
- **Multi-agent workers** — core/workers.py: bounded parallel specialists,
  sub-budgets (Budget.child()), isolated cancellation; `ultron fanout`.
- **Self-monitoring / self-diagnostics** — core/supervisor.py: circuit
  breakers, health snapshots, ComponentDegraded/Recovered events,
  FallbackChain (detect broken blocks, switch fallbacks).
- **Internet research** — tools/web_fetch.py (projected text) + agent loop;
  skills compose research workflows on top.
- **Computer control (files)** — FileTool + verifier + policy + approval hook;
  writes workspace-contained.
- **Hot-swappable blocks** — cold-swap proven across model/browser/tool/
  verifier/memory/policy; supervisor adds health-aware selection.
- **Shared memory/state (single node)** — contracts/memory.py + SQLite store;
  chat/schedule recall across sessions.
- **Permission hierarchy (action-level)** — policy allow/deny per action kind
  + risk classes + human approval hook; per-device levels land with fabric.

## SCAFFOLDED (contracts ready, no wire protocol yet)

- **Distributed presence / device discovery / capability migration / shared
  cross-device memory / cross-device continuity** — contracts/fabric.py:
  NodeIdentity, CapabilityAdvertisement (advertising != authorization),
  PairingRequest/Grant (human_approval_token REQUIRED — the 07 invariant,
  enforced by ContractViolationError), PairingLedger, FabricHost protocol.
  Next: transport (TLS/mDNS/libp2p decision), node auth keys, task migration
  envelopes, memory sync protocol.
- **Dynamic resource use (CPU/GPU/device)** — registry already models
  capabilities/health; router takes resource inputs; needs local hardware
  probes (GPU presence via ollama/llama.cpp backends).
- **Natural-language device control / smart-home / IoT** — nothing until
  fabric + a ToolProvider per device family; policy risk classes ready.

## DEFERRED (needs hardware/deps/design)

- **Voice interaction** (wake word, STT, TTS, interruptions) — voice block
  behind a stable contract is V0.5 per 08; whisper/piper bricks fit the
  ToolProvider/ModelProvider shapes.
- **Vision** (camera/screenshot understanding) — needs vision-capable model
  + image plumbing through contracts (ModelCapabilities.vision exists).
- **Computer control (desktop apps, terminal)** — a shell/desktop ToolProvider
  is deliberately NOT built yet: needs a dedicated policy profile
  (ask-by-default per action) + sandbox story before it can ship safely.
- **Context awareness** (active apps, location, current task) — partially
  derivable from plans+memory; OS integration deferred.

## Non-goals restated

No autonomous host acquisition. The model never authorizes privileged
actions. A discovered node is not an admitted node.

## Update 2026-08-22 (fundamentals pass)

- **Streaming** — BUILT: optional StreamingModel contract + both provider
  bricks (SSE/NDJSON), `ultron ask --stream` (live-verified).
- **Content-addressed artifacts** — BUILT: core/artifacts.py; compiler +
  agent loop offload bulk text to references (01 principle 4).
- **Identity** — BUILT: core/identity.py persisted instance identity, fabric
  bridge (`ultron identity`).
- **PostgreSQL memory** — BUILT: adapters/memory/postgres.py (optional group
  `postgres`, ULTRON_PG_DSN-gated tests), migration parity with SQLite.
- **Mechanical CI** — BUILT: .github/workflows/ci.yml (uv + pytest, Linux +
  Windows).
- **Security tests** — BUILT: tool-output injection, poisoned memory recall,
  forged kinds, lying tools, scheme gating (tests/test_agent_security.py).
