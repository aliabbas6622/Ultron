"""ULTRON model-provider bricks + registry. Depends on contracts/ only.

Providers are LEGO bricks: local Ollama, Ollama Cloud, any OpenAI-compatible
endpoint. Config lives in <ULTRON_HOME>/providers.toml (default ~/.ultron),
secrets in <ULTRON_HOME>/secrets.json via the SecretStore contract — never in
the config file, never in logs, never committed.
"""

from __future__ import annotations
