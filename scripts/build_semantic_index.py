#!/usr/bin/env python3
"""Default dry-run of offline embeddings for this app's pinned public pack."""

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
from server.semantic import MAX_BATCH, SemanticIndexError, dry_run_summary, prepare_index, request_embeddings, sha256, verify_public_pack


def plan() -> tuple[dict, list[str]]:
    root = PROJECT / "data/context"
    if not root.resolve().is_relative_to(PROJECT.resolve()):
        raise SemanticIndexError("Public context path escapes this app")
    # Verify public code before importing its document adapter.
    verify_public_pack(root)
    index = ContextIndex(root)
    index.load()
    return prepare_index(root, index.documents)


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


async def build(metadata: dict, inputs: list[str], token: str) -> dict:
    if not token:
        raise SemanticIndexError("Set the server-only AI_BUILDER_TOKEN to build the public index")
    vectors = array("f")
    tokens = 0
    async with httpx.AsyncClient(follow_redirects=False) as client:
        for start in range(0, len(inputs), MAX_BATCH):
            rows, used = await request_embeddings(client, token, inputs[start:start + MAX_BATCH], timeout=45)
            for row in rows:
                vectors.extend(row)
            tokens += used
            print(json.dumps({"stage": "embedding", "completed_windows": min(start + MAX_BATCH, len(inputs)), "total_windows": len(inputs)}), flush=True)
    root = PROJECT / "data/context"
    if sha256((root / "release-manifest.json").read_bytes()) != metadata["release_manifest_sha256"]:
        raise SemanticIndexError("Public release changed during indexing; no index was published")
    verify_public_pack(root)
    if sys.byteorder != "little":
        vectors.byteswap()
    raw = vectors.tobytes()
    checksum = sha256(raw)
    metadata = {**metadata, "vector_file": "vectors-" + checksum + ".f32", "vector_sha256": checksum, "build_usage_tokens": tokens}
    output = PROJECT / "data/semantic"
    if not output.resolve().is_relative_to((PROJECT / "data").resolve()):
        raise SemanticIndexError("Semantic output path escapes this app's public data directory")
    output.mkdir(parents=True, exist_ok=True)
    _write_atomic(output / metadata["vector_file"], raw)
    _write_atomic(output / "index.json", (json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
    return {"mode": "built", "path": "data/semantic", "embedding_windows": len(inputs), "vector_bytes": len(raw),
            "usage_tokens": tokens, "release_manifest_sha256": metadata["release_manifest_sha256"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Embed only the displayed, verified public primary chunks; defaults to dry-run")
    args = parser.parse_args(argv)
    try:
        metadata, inputs = plan()
        if not args.build:
            print(json.dumps(dry_run_summary(metadata, inputs), ensure_ascii=False, indent=2))
            return 0
        token = os.environ.get("AI_BUILDER_TOKEN", "")
        print(json.dumps(asyncio.run(build(metadata, inputs, token)), ensure_ascii=False, indent=2))
        return 0
    except SemanticIndexError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
