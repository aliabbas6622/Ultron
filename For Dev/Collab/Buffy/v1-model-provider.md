# Buffy — v1: ModelProvider v1 Contract

## Scope
adapters/model/ folder only. Implementing ModelProvider v1 contract:
- text_generation, cancellation, health, usage_reporting (required)
- Streaming, tool_calling (optional)
- DeepSeek adapter (remote, OpenAI-compatible)
- Ollama adapter (local, OpenAI-compatible)
- Conformance test suite

## Files
- adapters/model/models.mojo — typed data structures
- adapters/model/contract.mojo — abstract trait + errors + CancellationToken
- adapters/model/deepseek.mojo — DeepSeek provider
- adapters/model/ollama.mojo — Ollama provider
- adapters/model/conformance.mojo — conformance test suite
- adapters/__init__.mojo, adapters/model/__init__.mojo — package inits

## Status
Compiling, fixing remaining TestSuite ownership issue in conformance.mojo.

## Notes
- Project is Mojo 1.0.0 on WSL2, uv for package management
- Python FFI (httpx) for HTTP calls
- Do NOT touch adapters/browser/, core/, or contracts/
