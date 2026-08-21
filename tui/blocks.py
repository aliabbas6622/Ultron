"""Demo blocks for the TUI: a real (stdlib-only) HTTP browser, an Ollama-backed
model (talks to a local `ollama serve` over HTTP — no Mojo/WSL bridge needed,
Ollama already speaks HTTP), and a stub extractive "model" fallback. None of
these are the vendor Mojo adapters in adapters/model — see For Dev/Collab for
that bridge's status. ponytail: regex HTML stripping, not a real parser — good
enough for the V0.1 "one sentence" projection, swap for html.parser if a
page's structure actually matters.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from contracts.browser import HealthStatus as BrowserHealth
from contracts.browser import PageProjection
from contracts.model import HealthStatus as ModelHealth
from contracts.model import ModelCapabilities, ModelResult
from core.errors import BlockUnavailableError
from core.run_context import RunContext

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


@dataclass
class HttpBrowser:
    """Real HTTP fetch + naive text projection. No JS execution, no Chromium."""

    block_id: str = "http_stub"

    def visit(self, run: RunContext, url: str, timeout_s: float) -> PageProjection:
        run.check_alive()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"unsupported scheme: {url}")
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                raw = resp.read(1_000_000).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BlockUnavailableError(f"fetch failed for {url}: {exc}") from exc

        run.check_alive()
        title_match = _TITLE_RE.search(raw)
        title = _WS_RE.sub(" ", title_match.group(1)).strip() if title_match else url
        body = _TAG_RE.sub(" ", raw)
        body = _ANY_TAG_RE.sub(" ", body)
        text = _WS_RE.sub(" ", body).strip()
        truncated = len(text) > 4000
        return PageProjection(url=url, title=title, text=text[:4000], truncated=truncated)

    def health(self) -> BrowserHealth:
        return BrowserHealth(healthy=True, detail="stdlib urllib, no JS/Chromium")


@dataclass
class StubSummarizerModel:
    """Extractive one-sentence 'summary' — first sentence of the page text. Not an LLM.
    Placeholder until adapters/model is bridged to Python (see Collab/ for status)."""

    block_id: str = "extractive_stub"
    capabilities: ModelCapabilities = ModelCapabilities()

    def generate(self, run: RunContext, prompt: str) -> ModelResult:
        run.check_alive()
        time.sleep(0.15)  # ponytail: fake latency so the TUI's "running" state is visible
        marker = "Page content:\n"
        idx = prompt.find(marker)
        text = prompt[idx + len(marker) :] if idx != -1 else prompt
        sentence_end = re.search(r"[.!?](\s|$)", text)
        sentence = text[: sentence_end.end()].strip() if sentence_end else text[:160].strip()
        if not sentence:
            sentence = "(no extractable content)"
        return ModelResult(
            text=sentence,
            input_tokens=len(prompt) // 4,
            output_tokens=len(sentence) // 4,
            finish_reason="stop",
        )

    def health(self) -> ModelHealth:
        return ModelHealth(healthy=True, detail="extractive stub, not an LLM")


@dataclass
class OllamaModel:
    """ModelProvider backed by a local `ollama serve` (http://localhost:11434).
    Install: https://ollama.com — then `ollama pull <model>` and `ollama serve`
    (the desktop app runs serve for you). No WSL/Mojo bridge involved; Ollama
    already exposes a plain HTTP API."""

    block_id: str = "ollama"
    model: str = field(default_factory=lambda: os.environ.get("OLLAMA_MODEL", "llama3.2"))
    base_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)

    def generate(self, run: RunContext, prompt: str) -> ModelResult:
        run.check_alive()
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise BlockUnavailableError(f"ollama generate failed ({self.model} @ {self.base_url}): {exc}") from exc

        run.check_alive()
        text = data.get("response", "").strip()
        if not text:
            raise BlockUnavailableError(f"ollama returned empty response: {data}")
        return ModelResult(
            text=text,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            finish_reason=data.get("done_reason", "stop"),
        )

    def health(self) -> ModelHealth:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3) as resp:
                tags = json.loads(resp.read().decode("utf-8")).get("models", [])
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return ModelHealth(healthy=False, detail=f"ollama unreachable at {self.base_url}: {exc}")
        names = [m.get("name", "") for m in tags]
        if names and not any(n.startswith(self.model) for n in names):
            return ModelHealth(healthy=False, detail=f"model {self.model!r} not pulled; have {names}")
        return ModelHealth(healthy=True, detail=f"ollama @ {self.base_url}, model={self.model}")


def select_model(model_name: str | None = None):
    """Pick the model brick: Ollama if it's up (and has the model pulled), else the
    extractive stub. ponytail: health-checked once per call, not cached — cheap local
    HTTP GET. Shared by the TUI and the headless CLI."""
    ollama = OllamaModel(model=model_name) if model_name else OllamaModel()
    if ollama.health().healthy:
        return ollama
    return StubSummarizerModel()
