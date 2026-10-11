"""A failed or moving source must not replace a valid consumer snapshot."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("context_sync_test", Path(__file__).resolve().parents[1] / "scripts/sync_context.py")
syncer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(syncer)
REAL_SOURCE_COMMIT = syncer.source_commit


@pytest.fixture
def release(tmp_path, monkeypatch):
    source, consumer = tmp_path / "source", tmp_path / "consumer"
    source.mkdir(); (consumer / "data/context").mkdir(parents=True)
    (consumer / "data/context/old.md").write_text("previous synthetic release")
    (consumer / "data/context-lock.json").write_text("previous synthetic lock")
    files = []
    for relative, blob in [("scripts/search.py", b"# synthetic parser"), ("corpus/public.md", b"synthetic public body")]:
        target = source / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(blob)
        files.append({"path": relative, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()})
    manifest = {"schema_version": 3, "repository": "sunyuzheng/lizheng-open-context", "intended_visibility": "public",
                "snapshot_at": "2026-10-11", "files": files, "counts": {}, "licenses": {}}
    (source / "release-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(syncer, "ROOT", consumer)
    monkeypatch.setattr(syncer, "source_commit", lambda *args: "a" * 40)
    return source, consumer


def assert_previous(consumer):
    assert (consumer / "data/context/old.md").read_text() == "previous synthetic release"
    assert (consumer / "data/context-lock.json").read_text() == "previous synthetic lock"
    assert sorted(p.name for p in (consumer / "data").iterdir()) == ["context", "context-lock.json"]


def test_moving_source_copies_the_verified_bytes_and_manifest(release, monkeypatch):
    source, consumer = release
    read = Path.read_bytes; original_manifest = (source / "release-manifest.json").read_bytes()
    def moving_read(path):
        blob = read(path)
        if path == source / "release-manifest.json":
            path.write_text("changed after verified read")
        if path == source / "corpus/public.md":
            path.write_text("changed after verified read")
        return blob
    monkeypatch.setattr(Path, "read_bytes", moving_read)
    lock = syncer.sync(source)
    assert (consumer / "data/context/corpus/public.md").read_bytes() == b"synthetic public body"
    assert (consumer / "data/context/release-manifest.json").read_bytes() == original_manifest
    assert lock["release_manifest_sha256"] == hashlib.sha256(original_manifest).hexdigest()


@pytest.mark.parametrize("corruption", ["source", "staging", "duplicate", "symlink"])
def test_invalid_candidate_leaves_both_previous_outputs_intact(release, monkeypatch, corruption):
    source, consumer = release
    if corruption == "source":
        (source / "corpus/public.md").write_text("different source")
    elif corruption == "staging":
        write = Path.write_bytes
        def bad_write(path, blob):
            return write(path, b"corrupt copy" if path.name == "public.md" else blob)
        monkeypatch.setattr(Path, "write_bytes", bad_write)
    elif corruption == "duplicate":
        p = source / "release-manifest.json"; manifest = json.loads(p.read_text()); manifest["files"].append(manifest["files"][0]); p.write_text(json.dumps(manifest))
    else:
        p = source / "corpus/public.md"; p.rename(source / "other.md"); p.symlink_to(source / "other.md")
    with pytest.raises(ValueError):
        syncer.sync(source)
    assert_previous(consumer)


def test_install_failure_restores_both_previous_outputs(release, monkeypatch):
    source, consumer = release
    rename = Path.rename
    def fail_install(path, target):
        if path.name == "context-lock.json" and path.parent.name != "data":
            raise OSError("synthetic failed lock installation")
        return rename(path, target)
    monkeypatch.setattr(Path, "rename", fail_install)
    with pytest.raises(OSError, match="synthetic failed"):
        syncer.sync(source)
    assert_previous(consumer)


def test_commit_pin_is_bound_to_the_exact_copied_manifest(release, monkeypatch):
    import subprocess
    source, consumer = release
    for args in [("init", "-q"), ("add", "."), ("-c", "user.name=Synthetic", "-c", "user.email=test@example.org", "commit", "-qm", "Synthetic release")]:
        subprocess.run(["git", "-C", str(source), *args], check=True)
    monkeypatch.setattr(syncer, "source_commit", REAL_SOURCE_COMMIT)
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    assert syncer.sync(source)["source_commit"] == head
    manifest_path = source / "release-manifest.json"
    manifest_path.write_text(manifest_path.read_text() + "\n")
    with pytest.raises(ValueError, match="immutable source commit"):
        syncer.sync(source)
    assert json.loads((consumer / "data/context-lock.json").read_text())["source_commit"] == head
