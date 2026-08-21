"""Content-addressed artifact store (04_TECH_STACK.md "Artifacts").

Large immutable data stays out of the prompt as a reference instead of raw
bulk (01 principle 4): the id IS the address -- "sha256-" + the hex digest of
the content -- so identical data can never be stored twice under different
ids and the reference a model sees is reproducible from content alone
(deterministic, principle 6).

Layout: <ULTRON_HOME>/artifacts/<id[7:9]>/<id> with optional sidecar
<id>.meta.json (written only when metadata is given). Two hex chars of
sharding keep any one directory small on fat stores. Writes are immutable
and atomic-ish: content is written to a temp file in the target shard and
os.replace()d into place only when the target does not exist yet, so a
crashed writer never leaves a half-written artifact under a valid address.

Backend: local filesystem initially, optional S3-compatible later (04).
Pure composition: stdlib only. ULTRON_HOME resolution mirrors
core/plans.py and providers/registry.py (duplicated because core never
imports providers).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

ID_PATTERN = r"sha256-[0-9a-f]{64}"
ID_RE = re.compile(rf"^{ID_PATTERN}$")
META_SUFFIX = ".meta.json"


def ultron_home() -> Path:
    return Path(os.environ.get("ULTRON_HOME", os.path.join(os.path.expanduser("~"), ".ultron")))


def default_artifacts_dir() -> Path:
    return ultron_home() / "artifacts"


def artifact_id_for(data: str | bytes) -> str:
    """Pure content address: sha256 of the bytes, no disk access. str is UTF-8."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256-" + hashlib.sha256(data).hexdigest()


def _check_id(artifact_id: str) -> str:
    """Validate id format (also guards path construction: only hex survives)."""
    if not isinstance(artifact_id, str) or not ID_RE.match(artifact_id):
        raise ValueError(f"invalid artifact id {artifact_id!r}: expected {ID_PATTERN}")
    return artifact_id


@dataclass(frozen=True)
class ArtifactRef:
    """Portable pointer to stored content. The prompt carries this, never the bulk."""

    artifact_id: str
    path: str
    size_bytes: int
    content_type: str = "text/plain"
    metadata: dict = field(default_factory=dict)


class ArtifactStore:
    """Immutable content-addressed blob store on the local filesystem."""

    def __init__(self, dir: Path | None = None) -> None:
        self._dir = Path(dir) if dir is not None else default_artifacts_dir()

    @property
    def dir(self) -> Path:
        return self._dir

    def _path_for(self, artifact_id: str) -> Path:
        _check_id(artifact_id)
        return self._dir / artifact_id[7:9] / artifact_id

    def store(self, data: str | bytes, content_type: str = "text/plain", metadata: dict | None = None) -> ArtifactRef:
        """Store content under its own hash. Writing existing content is a no-op
        (immutability): the blob and its address are already correct on disk."""
        raw = data.encode("utf-8") if isinstance(data, str) else data
        artifact_id = artifact_id_for(raw)
        path = self._path_for(artifact_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=artifact_id + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                os.replace(tmp_name, path)
            except BaseException:
                with suppress(OSError):
                    os.unlink(tmp_name)
                raise
        if metadata:
            meta_path = path.with_name(path.name + META_SUFFIX)
            meta_path.write_text(
                json.dumps({"content_type": content_type, "metadata": metadata}, sort_keys=True, indent=2),
                encoding="utf-8",
            )
        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(path),
            size_bytes=len(raw),
            content_type=content_type,
            metadata=dict(metadata) if metadata else {},
        )

    def load(self, artifact_id: str) -> bytes:
        """Full content by id. ValueError on invalid format or unknown id."""
        path = self._path_for(artifact_id)
        if not path.is_file():
            raise ValueError(f"unknown artifact id {artifact_id!r}: not in store")
        return path.read_bytes()

    def exists(self, artifact_id: str) -> bool:
        try:
            path = self._path_for(artifact_id)
        except ValueError:
            return False
        return path.is_file()

    def ref(self, artifact_id: str) -> ArtifactRef:
        """Pointer for an existing artifact; reads the metadata sidecar if present.
        ValueError on invalid format or unknown id."""
        path = self._path_for(artifact_id)
        if not path.is_file():
            raise ValueError(f"unknown artifact id {artifact_id!r}: not in store")
        content_type = "text/plain"
        metadata: dict = {}
        meta_path = path.with_name(path.name + META_SUFFIX)
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            content_type = str(meta.get("content_type", content_type))
            metadata = dict(meta.get("metadata") or {})
        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(path),
            size_bytes=path.stat().st_size,
            content_type=content_type,
            metadata=metadata,
        )

    def list_ids(self) -> list[str]:
        """All stored artifact ids, sorted. Sidecar meta files are not artifacts."""
        ids: set[str] = set()
        if self._dir.is_dir():
            for shard in self._dir.iterdir():
                if shard.is_dir():
                    for entry in shard.iterdir():
                        if ID_RE.match(entry.name):
                            ids.add(entry.name)
        return sorted(ids)
