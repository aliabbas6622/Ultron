# adapters.model — Model block implementations behind the ModelProvider v1 contract.
#
# Required capabilities: text_generation, cancellation, health, usage_reporting.
# Optional capabilities: streaming, vision, audio, tool_calling, embeddings,
#                        prompt_cache, structured_output.
#
# Vendors supported:
#   - DeepSeek  (remote, OpenAI-compatible API)
#   - Ollama    (local,  OpenAI-compatible endpoint)
