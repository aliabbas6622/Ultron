"""Artifact store unit checks: content addressing, immutability, sharded
layout, metadata sidecars, and ULTRON_HOME routing (04_TECH_STACK.md
"Artifacts": content-addressed local filesystem, hash-based ids)."""

from __future__ import annotations

import json

import pytest

from core.artifacts import ArtifactRef, ArtifactStore, artifact_id_for


def test_id_is_stable_and_content_derived():
    # same data -> same id, different data -> different id
    assert artifact_id_for("hello") == artifact_id_for("hello")
    assert artifact_id_for("hello") != artifact_id_for("hellO")
    # str and its UTF-8 bytes address the same content
    assert artifact_id_for("hello") == artifact_id_for(b"hello")
    assert artifact_id_for(b"hello") == artifact_id_for(b"hello")
    aid = artifact_id_for("hello")
    assert aid.startswith("sha256-") and len(aid) == 7 + 64
    int(aid[7:], 16)  # id tail must be 64 hex chars


def test_store_load_round_trip_str_and_bytes(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    text_ref = store.store("some page text")
    blob_ref = store.store(b"\x00binary\xff", content_type="application/octet-stream")
    assert store.load(text_ref.artifact_id) == b"some page text"
    assert store.load(blob_ref.artifact_id) == b"\x00binary\xff"
    assert text_ref.size_bytes == len(b"some page text")
    assert blob_ref.size_bytes == len(b"\x00binary\xff")


def test_store_is_immutable_storing_twice_changes_nothing(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    first = store.store("payload", metadata={"k": "v"})
    path = tmp_path / first.artifact_id[7:9] / first.artifact_id
    before = path.read_bytes()
    second = store.store("payload", metadata={"k": "v"})  # no error, same address
    assert second.artifact_id == first.artifact_id
    assert path.read_bytes() == before == b"payload"
    assert store.load(first.artifact_id) == b"payload"


def test_sharded_path_layout(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    ref = store.store("shard me")
    shard = ref.artifact_id[7:9]
    assert ref.path == str(tmp_path / shard / ref.artifact_id)
    assert (tmp_path / shard / ref.artifact_id).is_file()
    # the blob lives inside its shard dir, never loose in the store root
    assert not (tmp_path / ref.artifact_id).exists()
    assert [p.name for p in tmp_path.iterdir()] == [shard]


def test_exists_and_list_ids(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    a = store.store("a")
    b = store.store("b")
    assert store.exists(a.artifact_id) and store.exists(b.artifact_id)
    assert store.list_ids() == sorted([a.artifact_id, b.artifact_id])
    unknown = artifact_id_for("never stored")
    assert not store.exists(unknown)
    assert not store.exists("not-an-id")  # invalid format is not an error here
    assert unknown not in store.list_ids()
    assert store.list_ids() == store.list_ids()  # sorted, hence deterministic


def test_metadata_round_trip(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    ref = store.store("with meta", content_type="text/html", metadata={"url": "https://x", "n": 2})
    meta_path = tmp_path / ref.artifact_id[7:9] / (ref.artifact_id + ".meta.json")
    assert meta_path.is_file()
    sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
    assert sidecar["metadata"] == {"url": "https://x", "n": 2}
    got = store.ref(ref.artifact_id)
    assert got.metadata == {"url": "https://x", "n": 2}
    assert got.content_type == "text/html"
    assert got.artifact_id == ref.artifact_id and got.path == ref.path
    assert got.size_bytes == ref.size_bytes == len(b"with meta")
    # no metadata -> no sidecar, empty dict, default content type
    plain = store.store("no meta")
    assert not (tmp_path / plain.artifact_id[7:9] / (plain.artifact_id + ".meta.json")).exists()
    plain_ref = store.ref(plain.artifact_id)
    assert plain_ref.metadata == {} and plain_ref.content_type == "text/plain"
    # sidecars are not artifacts
    assert plain.artifact_id + ".meta.json" not in store.list_ids()


def test_load_and_ref_raise_value_error_on_unknown_or_invalid_id(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    with pytest.raises(ValueError):
        store.load(artifact_id_for("missing"))
    with pytest.raises(ValueError):
        store.ref(artifact_id_for("missing"))
    for bad in ("", "sha256-", "sha256-zz" + "0" * 61, "md5-abc", "../escape"):
        with pytest.raises(ValueError):
            store.load(bad)


def test_ultron_home_override_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path))
    store = ArtifactStore()  # no explicit dir -> <ULTRON_HOME>/artifacts
    assert store.dir == tmp_path / "artifacts"
    ref = store.store("env routed")
    assert (tmp_path / "artifacts" / ref.artifact_id[7:9] / ref.artifact_id).is_file()


def test_explicit_dir_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path / "elsewhere"))
    store = ArtifactStore(dir=tmp_path / "explicit")
    ref = store.store("x")
    assert (tmp_path / "explicit" / ref.artifact_id[7:9] / ref.artifact_id).is_file()
    assert not (tmp_path / "elsewhere").exists()


def test_artifact_ref_field_defaults():
    ref = ArtifactRef(artifact_id="sha256-" + "0" * 64, path="/x/y", size_bytes=1)
    assert ref.content_type == "text/plain"
    assert ref.metadata == {}
