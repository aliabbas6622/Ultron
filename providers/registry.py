"""Provider registry: add/edit/remove providers, pick the default, build bricks.

Config:  <ULTRON_HOME>/providers.toml   (TOML per 04_TECH_STACK; shareable)
Secrets: <ULTRON_HOME>/secrets.json     (owner-only; referenced by name, never logged)

ULTRON_HOME defaults to ~/.ultron. Depends on contracts/ + providers/ only —
no core import, so an external host can use the same registry.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from providers.models import OllamaLocalModel, OpenAICompatModel

KINDS = ("ollama", "ollama-cloud", "openai-compatible")
_DEFAULT_KIND_BASE_URLS = {
    "ollama": "http://localhost:11434",
    "ollama-cloud": "https://ollama.com",
    "openai-compatible": "https://api.deepseek.com/v1",
}


def ultron_home() -> Path:
    return Path(os.environ.get("ULTRON_HOME", Path.home() / ".ultron"))


class FileSecretStore:
    """SecretStore over a JSON file, owner-only permissions where the platform allows."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._cache is None:
            if self._path.is_file():
                self._cache = json.loads(self._path.read_text(encoding="utf-8"))
            else:
                self._cache = {}
        return self._cache

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._load(), indent=2), encoding="utf-8")
        try:
            os.chmod(self._path, 0o600)
        except OSError:  # windows best-effort
            pass

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def set(self, name: str, value: str) -> None:
        self._load()[name] = value
        self._flush()

    def delete(self, name: str) -> None:
        if name in self._load():
            del self._load()[name]
            self._flush()

    def list(self) -> list[str]:
        return sorted(self._load())


@dataclass
class ProviderSpec:
    name: str
    kind: str
    base_url: str
    model: str = ""
    api_key_secret: str | None = None  # secret-store key name; None = no auth

    def to_row(self) -> str:
        auth = "key" if self.api_key_secret else "no-auth"
        return f"{self.name:<18} {self.kind:<18} {self.model:<28} {auth:<8} {self.base_url}"


@dataclass
class ProviderRegistry:
    home: Path = field(default_factory=ultron_home)
    default_provider: str | None = None
    default_model: str | None = None  # global override; falls back to provider's model
    specs: dict[str, ProviderSpec] = field(default_factory=dict)

    # --- paths -------------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self.home / "providers.toml"

    @property
    def secrets_path(self) -> Path:
        return self.home / "secrets.json"

    @property
    def secrets(self) -> FileSecretStore:
        return FileSecretStore(self.secrets_path)

    # --- persistence -------------------------------------------------------

    @classmethod
    def load(cls, home: Path | None = None) -> "ProviderRegistry":
        home = home or ultron_home()
        reg = cls(home=home)
        cfg_path = home / "providers.toml"
        if cfg_path.is_file():
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
            reg.default_provider = raw.get("default_provider")
            reg.default_model = raw.get("default_model")
            for name, entry in (raw.get("providers") or {}).items():
                reg.specs[name] = ProviderSpec(
                    name=name,
                    kind=entry.get("kind", "openai-compatible"),
                    base_url=entry.get("base_url", ""),
                    model=entry.get("model", ""),
                    api_key_secret=entry.get("api_key_secret"),
                )
        else:
            reg._seed_defaults()
        return reg

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if self.default_provider:
            lines.append(f"default_provider = {_toml_str(self.default_provider)}")
        if self.default_model:
            lines.append(f"default_model = {_toml_str(self.default_model)}")
        for spec in self.specs.values():
            lines.append(f"\n[providers.{spec.name}]")
            lines.append(f"kind = {_toml_str(spec.kind)}")
            lines.append(f"base_url = {_toml_str(spec.base_url)}")
            if spec.model:
                lines.append(f"model = {_toml_str(spec.model)}")
            if spec.api_key_secret:
                lines.append(f"api_key_secret = {_toml_str(spec.api_key_secret)}")
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _seed_defaults(self) -> None:
        self.specs["ollama-local"] = ProviderSpec(
            name="ollama-local", kind="ollama",
            base_url=_DEFAULT_KIND_BASE_URLS["ollama"], model="llama3.2",
        )
        self.default_provider = "ollama-local"
        self.save()

    # --- CRUD ----------------------------------------------------------------

    def add(self, name: str, kind: str, base_url: str = "", model: str = "", api_key: str | None = None,
            set_default: bool = False) -> ProviderSpec:
        if name in self.specs:
            raise ValueError(f"provider {name!r} already exists (use edit)")
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
        spec = ProviderSpec(
            name=name, kind=kind,
            base_url=base_url or _DEFAULT_KIND_BASE_URLS[kind],
            model=model,
            api_key_secret=name if api_key else None,
        )
        if api_key:
            self.secrets.set(name, api_key)
        self.specs[name] = spec
        if set_default or not self.default_provider:
            self.default_provider = name
        self.save()
        return spec

    def edit(self, name: str, base_url: str | None = None, model: str | None = None,
             api_key: str | None = None, kind: str | None = None) -> ProviderSpec:
        if name not in self.specs:
            raise ValueError(f"unknown provider {name!r}")
        spec = self.specs[name]
        if kind and kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
        updates: dict = {}
        if kind:
            updates["kind"] = kind
        if base_url:
            updates["base_url"] = base_url
        if model is not None:  # allow clearing with ""
            updates["model"] = model
        if api_key:
            self.secrets.set(name, api_key)
            updates["api_key_secret"] = name
        self.specs[name] = replace(spec, **updates)
        self.save()
        return self.specs[name]

    def remove(self, name: str) -> None:
        if name not in self.specs:
            raise ValueError(f"unknown provider {name!r}")
        del self.specs[name]
        self.secrets.delete(name)
        if self.default_provider == name:
            self.default_provider = next(iter(self.specs), None)
        self.save()

    def set_default(self, name: str, model: str | None = None) -> None:
        if name not in self.specs:
            raise ValueError(f"unknown provider {name!r}")
        self.default_provider = name
        self.default_model = model or self.specs[name].model
        self.save()

    # --- brick factory --------------------------------------------------------

    def default_model_name(self, provider: str | None = None) -> str:
        name = provider or self.default_provider
        if name is None or name not in self.specs:
            return ""
        if not provider and self.default_model:
            return self.default_model
        return self.specs[name].model or self.default_model or ""

    def build(self, name: str | None = None, model: str | None = None):
        """Build a ModelProvider brick for a configured provider."""
        name = name or self.default_provider
        if name is None or name not in self.specs:
            raise ValueError("no provider configured (ultron providers add)")
        spec = self.specs[name]
        model = model or self.default_model_name(name)
        api_key = self.secrets.get(spec.api_key_secret) if spec.api_key_secret else None
        if spec.kind == "ollama":
            return OllamaLocalModel(model=model or "llama3.2", base_url=spec.base_url)
        if spec.kind == "ollama-cloud":
            return OpenAICompatModel(model=model, base_url=spec.base_url, api_key=api_key,
                                     block_id=f"ollama-cloud.{spec.name}")
        return OpenAICompatModel(model=model, base_url=spec.base_url, api_key=api_key,
                                 block_id=f"openai.{spec.name}")


def _toml_str(value: str) -> str:
    # JSON string escaping is valid TOML basic-string escaping for our charset
    return json.dumps(value, ensure_ascii=False)
