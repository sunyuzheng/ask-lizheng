#!/usr/bin/env python3
"""Default dry-run of offline embeddings for this app's pinned public pack.

A build keeps the vector of every window whose exact text the current index already embeds, and asks
the embedding service only for new or changed windows; --full embeds everything again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from array import array
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import httpx

from server.retrieval import ContextIndex
from server.semantic import (DIMENSIONS, EMBEDDING_MODEL, MAX_BATCH, VECTOR_FILE, SemanticIndexError, dry_run_summary,
                             prepare_index, request_embeddings, sha256, verify_public_pack)


def plan() -> tuple[dict, list[str]]:
    root = PROJECT / "data/context"
    if not root.resolve().is_relative_to(PROJECT.resolve()):
        raise SemanticIndexError("Public context path escapes this app")
    # Verify public code before importing its document adapter.
    verify_public_pack(root)
    index = ContextIndex(root)
    index.load()
    return prepare_index(root, index.documents)


def reusable(output: Path) -> dict[str, bytes]:
    """The current index's vectors by the hash of the exact text each embeds, or nothing when the
    index was built another way or fails its checksum."""
    try:
        metadata = json.loads((output / "index.json").read_text(encoding="utf-8"))
        if (metadata.get("schema_version"), metadata.get("model"), metadata.get("dimensions"), metadata.get("byte_order"),
                metadata.get("normalized")) != (1, EMBEDDING_MODEL, DIMENSIONS, "little", True):
            return {}
        name, checksum, entries = metadata["vector_file"], metadata["vector_sha256"], metadata["entries"]
        if name not in (VECTOR_FILE, "vectors-" + checksum + ".f32"):
            return {}
        raw = (output / name).read_bytes()
        width = DIMENSIONS * 4
        if sha256(raw) != checksum or len(raw) != len(entries) * width:
            return {}
        return {row["input_sha256"]: raw[index * width:(index + 1) * width] for index, row in enumerate(entries)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def _write_atomic(path: Path, data: bytes):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temp_path = Path(temporary.name)
    try:
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


async def build(metadata: dict, inputs: list[str], token: str, reuse: dict[str, bytes] | None = None) -> dict:
    reuse = reuse or {}
    rows = [reuse.get(row["input_sha256"]) for row in metadata["entries"]]
    missing = [index for index, row in enumerate(rows) if row is None]
    if missing and not token:
        raise SemanticIndexError("Set the server-only AI_BUILDER_TOKEN to build the public index")
    tokens = 0
    if missing:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            for start in range(0, len(missing), MAX_BATCH):
                batch = missing[start:start + MAX_BATCH]
                vectors, used = await request_embeddings(client, token, [inputs[index] for index in batch], timeout=45)
                for index, vector in zip(batch, vectors):
                    if sys.byteorder != "little":
                        vector.byteswap()
                    rows[index] = vector.tobytes()
                tokens += used
                print(json.dumps({"stage": "embedding", "completed_windows": min(start + MAX_BATCH, len(missing)), "total_windows": len(missing)}), flush=True)
    root = PROJECT / "data/context"
    if sha256((root / "release-manifest.json").read_bytes()) != metadata["release_manifest_sha256"]:
        raise SemanticIndexError("Public release changed during indexing; no index was published")
    verify_public_pack(root)
    raw = b"".join(rows)
    checksum = sha256(raw)
    metadata = {**metadata, "vector_file": VECTOR_FILE, "vector_sha256": checksum, "build_usage_tokens": tokens}
    output = PROJECT / "data/semantic"
    if not output.resolve().is_relative_to((PROJECT / "data").resolve()):
        raise SemanticIndexError("Semantic output path escapes this app's public data directory")
    output.mkdir(parents=True, exist_ok=True)
    _write_atomic(output / VECTOR_FILE, raw)
    _write_atomic(output / "index.json", (json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
    for stale in output.glob("vectors-*.f32"):
        stale.unlink()
    return {"mode": "built", "path": "data/semantic", "embedding_windows": len(inputs), "reused_windows": len(inputs) - len(missing),
            "embedded_windows": len(missing), "vector_bytes": len(raw), "usage_tokens": tokens,
            "release_manifest_sha256": metadata["release_manifest_sha256"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Embed only the displayed, verified public primary chunks; defaults to dry-run")
    parser.add_argument("--full", action="store_true", help="Embed every window again instead of keeping unchanged vectors")
    args = parser.parse_args(argv)
    try:
        metadata, inputs = plan()
        reuse = {} if args.full else reusable(PROJECT / "data/semantic")
        if not args.build:
            new = sum(row["input_sha256"] not in reuse for row in metadata["entries"])
            print(json.dumps({**dry_run_summary(metadata, inputs), "reused_windows": len(inputs) - new, "new_windows": new},
                             ensure_ascii=False, indent=2))
            return 0
        token = os.environ.get("AI_BUILDER_TOKEN", "")
        print(json.dumps(asyncio.run(build(metadata, inputs, token, reuse)), ensure_ascii=False, indent=2))
        return 0
    except SemanticIndexError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
