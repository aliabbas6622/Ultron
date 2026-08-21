# Tech Stack

## Language Strategy

**Mojo-first, not Mojo-blind.**

Mojo remains the preferred language for:
- context compilation
- routing logic
- policy logic
- typed state
- token/format optimization
- performance-sensitive local processing

Before making Mojo own all orchestration, prove required runtime capabilities with a spike:
- concurrent model calls
- cancellation
- PostgreSQL query
- subprocess supervision/restart
- HTTP client path

If this is unreasonably painful, preserve all contracts and move control-plane orchestration to Rust or Go while keeping Mojo for high-value compute/context paths.

## Browser

Correctness baseline: Chromium-compatible backend.

Fast path: Lightpanda.

Routing decides which to use.

```text
simple extraction/research -> Lightpanda
complex SPA/auth/upload    -> Chromium
```

Do not assume one browser backend is universally superior.

## Memory Persistence

Default:
- PostgreSQL
- pgvector where semantic retrieval is useful

Development:
- SQLite may be used for simple local testing

Use migrations from day one.

STATUS 2026-08-22: SQLite dev backend (core/memory_sqlite.py) + conformance
suite landed earlier. PostgreSQL backend landed at adapters/memory/postgres.py
(optional dep group `postgres`: `uv sync --group postgres`; tests run when
ULTRON_PG_DSN is set) — same MemoryProvider v1 semantics, same migration
versioning. pgvector semantic retrieval remains open until retrieval needs it.

## External Tool Standard

Use MCP at external boundaries where useful.

Treat MCP servers and tool descriptions as untrusted capability sources.

Do not use MCP as the internal protocol for every module.

## LLM-Facing Encoding

Auto-select among:
- TOON
- compact JSON
- plain text
- tabular text

TOON is deferred until the context compiler exists and benchmarks show it wins for the active model/data shape.

## Local Models

Initial adapter may support Ollama or an OpenAI-compatible local endpoint.

Do not couple routing to one local inference engine.

## Artifacts

Large immutable data:
- content-addressed local filesystem initially
- hash-based IDs
- optional S3-compatible backend later

STATUS 2026-08-22: implemented — core/artifacts.py ArtifactStore (sha256 ids,
sharded layout, immutable writes, metadata sidecars, default
~/.ultron/artifacts). The context compiler offloads over-threshold page text
into artifact references, and the agent loop offloads large tool outputs the
same way (references over raw bulk, 01 principle 4).

## Config

TOML for normal configuration.

Secrets through a `SecretStore` contract.

Implemented (providers): `<ULTRON_HOME>/providers.toml` (default `~/.ultron`)
holds the provider registry — kinds `ollama`, `ollama-cloud`,
`openai-compatible` — and `<ULTRON_HOME>/secrets.json` is the SecretStore
(`contracts/secret_store.py`, owner-only perms, referenced by name, never
logged, never committed). Manage via `ultron providers add|edit|remove|
default|test` or the `providers.registry.ProviderRegistry` API.
