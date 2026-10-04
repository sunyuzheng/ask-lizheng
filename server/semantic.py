"""Small, read-only public semantic candidate index; lexical fallback is safe.

Only offline public source vectors are persisted. A runtime query is embedded
once, never cached, logged, or written. No pickle, NumPy, local model, or observer.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import math
import re
import sys
from array import array
from pathlib import Path

import httpx

from .context_architecture import _primary, _safe_path, _verified_file


EMBEDDINGS_URL = "https://space.ai-builders.com/backend/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-3-small"
DIMENSIONS = 256
MAX_INPUT_BYTES = 7000
MAX_INDEX_VECTORS = 20_000
MAX_BATCH = 32
# One name, so each rebuild stores only the vectors that changed in Git; index.json pins the bytes.
VECTOR_FILE = "vectors.f32"
MIN_SIMILARITY = .40
PUBLIC_REPOSITORY = "sunyuzheng/lizheng-open-context"


class SemanticIndexError(ValueError):
    """Fixed error messages that never include credentials or question text."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def document_hash(doc) -> str:
    return sha256(doc.text.encode("utf-8"))


def verify_public_pack(root: Path) -> tuple[dict, dict]:
    try:
        manifest_path = root / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("repository") != PUBLIC_REPOSITORY or manifest.get("intended_visibility") != "public":
            raise SemanticIndexError("Expected the explicitly public pinned context pack")
        rows = manifest["files"]
        records = {row["path"]: row for row in rows}
        if len(records) != len(rows) or len(rows) > 10_000:
            raise SemanticIndexError("Invalid public release file list")
        for relative in records:
            if relative != ".gitignore" and any(part.startswith(".") for part in Path(relative).parts):
                raise SemanticIndexError("Hidden paths are not public index inputs")
            _verified_file(root, relative, records)
        return manifest, records
    except SemanticIndexError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise SemanticIndexError("Public context manifest or source hashes are invalid") from None


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[\d\d:\d\d:\d\d\]", " ", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"\s+", " ", text.replace("`", "")).strip()


def _byte_windows(text: str, limit: int):
    current, size = [], 0
    for character in text:
        width = len(character.encode("utf-8"))
        if current and size + width > limit:
            yield "".join(current)
            current, size = [], 0
        current.append(character)
        size += width
    if current:
        yield "".join(current)


def embedding_inputs(doc) -> list[str]:
    prefix = _plain(doc.title[:300] + " / " + doc.section[:180]) + "\n"
    prefix = next(_byte_windows(prefix, 1800), "")
    # Byte count is a conservative token bound even for rare Unicode. Keep
    # every part of long public chunks instead of silently cutting their tails.
    limit = MAX_INPUT_BYTES - len(prefix.encode("utf-8"))
    return [prefix + part for part in _byte_windows(_plain(doc.text), limit)]


def prepare_index(root: Path, documents) -> tuple[dict, list[str]]:
    manifest, records = verify_public_pack(root)
    entries, inputs, paths, public_bodies = [], [], set(), {}
    seen = set()
    for doc in documents:
        if not _primary(doc):
            continue
        if doc.path not in records:
            raise SemanticIndexError("A primary document is absent from the public release")
        if doc.path not in public_bodies:
            raw = _safe_path(root, doc.path).read_bytes()
            if sha256(raw) != records[doc.path]["sha256"]:
                raise SemanticIndexError("A public source changed while preparing the index")
            public_bodies[doc.path] = _plain(raw.decode("utf-8"))
        if _plain(doc.text) not in public_bodies[doc.path]:
            raise SemanticIndexError("Embedding text is not a body from the pinned public source")
        identity = (doc.path, doc.id)
        if identity in seen:
            raise SemanticIndexError("Duplicate public document identity")
        seen.add(identity)
        paths.add(doc.path)
        for segment, text in enumerate(embedding_inputs(doc)):
            inputs.append(text)
            entries.append({"path": doc.path, "document_id": doc.id, "text_sha256": document_hash(doc),
                            "segment": segment, "input_sha256": sha256(text.encode("utf-8"))})
    if len(entries) > MAX_INDEX_VECTORS or not entries:
        raise SemanticIndexError("Public semantic index is empty or exceeds its size bound")
    metadata = {
        "schema_version": 1, "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS,
        "byte_order": "little", "normalized": True, "repository": PUBLIC_REPOSITORY,
        "snapshot_at": manifest.get("snapshot_at", ""),
        "release_manifest_sha256": sha256((root / "release-manifest.json").read_bytes()),
        "files": [{"path": path, "sha256": records[path]["sha256"]} for path in sorted(paths)],
        "entries": entries,
    }
    return metadata, inputs


def dry_run_summary(metadata: dict, inputs: list[str]) -> dict:
    characters = sum(map(len, inputs))
    non_ascii = sum(sum(ord(character) > 127 for character in text) for text in inputs)
    ascii_chars = characters - non_ascii
    return {
        "mode": "dry-run", "destination": EMBEDDINGS_URL,
        "audience": "AI Builder's configured OpenAI-compatible embeddings provider",
        "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS,
        "release_manifest_sha256": metadata["release_manifest_sha256"],
        "public_primary_chunks": len({(row["path"], row["document_id"]) for row in metadata["entries"]}),
        "embedding_windows": len(inputs), "input_characters": characters,
        "estimated_tokens_heuristic": {"lower": math.ceil(ascii_chars / 5 + non_ascii * .75), "upper": math.ceil(ascii_chars / 3 + non_ascii * 2)},
        "input_utf8_bytes_upper_token_bound": sum(len(text.encode("utf-8")) for text in inputs),
        "vector_bytes": len(inputs) * DIMENSIONS * 4,
        "batch_size": MAX_BATCH, "estimated_requests": math.ceil(len(inputs) / MAX_BATCH),
        "files": metadata["files"],
    }


def normalized_vector(values) -> array:
    if not isinstance(values, list) or len(values) != DIMENSIONS:
        raise SemanticIndexError("Embedding provider returned an invalid dimension")
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
        raise SemanticIndexError("Embedding provider returned non-finite values")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 1e-10:
        raise SemanticIndexError("Embedding provider returned an empty vector")
    return array("f", [value / norm for value in values])


async def request_embeddings(client: httpx.AsyncClient, token: str, inputs: list[str], timeout: float) -> tuple[list[array], int]:
    if not token or not inputs or len(inputs) > MAX_BATCH or any(len(text.encode("utf-8")) > MAX_INPUT_BYTES for text in inputs):
        raise SemanticIndexError("Embedding request exceeds its public input bounds")
    try:
        async with asyncio.timeout(timeout):
            response = await client.post(
                EMBEDDINGS_URL,
                headers={"Authorization": "Bearer " + token},
                json={"model": EMBEDDING_MODEL, "input": inputs, "dimensions": DIMENSIONS, "encoding_format": "float"},
                timeout=httpx.Timeout(timeout, connect=min(5, timeout), pool=min(3, timeout)),
                follow_redirects=False,
            )
            if not response.is_success:
                raise SemanticIndexError("Embedding provider is unavailable")
            data = response.json()
            rows = data.get("data")
            if data.get("model") != EMBEDDING_MODEL or not isinstance(rows, list) or len(rows) != len(inputs):
                raise SemanticIndexError("Embedding provider returned an incompatible response")
            ordered = {}
            for row in rows:
                if type(row.get("index")) is not int or not 0 <= row["index"] < len(inputs) or row["index"] in ordered:
                    raise SemanticIndexError("Embedding provider returned invalid row indexes")
                ordered[row["index"]] = normalized_vector(row.get("embedding"))
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return [ordered[index] for index in range(len(inputs))], tokens if type(tokens) is int and tokens >= 0 else 0
    except SemanticIndexError:
        raise
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise SemanticIndexError("Embedding request failed or timed out") from None


class SemanticIndex:
    def __init__(self, root: Path, documents, *, index_root: Path | None = None):
        self.root = Path(root)
        self.documents = documents
        self.index_root = Path(index_root) if index_root else self.root.parent / "semantic"
        self.ready = False
        self.disabled_reason = "missing-index"
        self.vectors = array("f")
        self.document_indexes = []

    def load(self) -> bool:
        self.ready = False
        self.vectors = array("f")
        self.document_indexes = []
        try:
            if not self.index_root.resolve().is_relative_to(self.root.parent.resolve()):
                raise SemanticIndexError("Semantic index path escapes this app's public data directory")
            metadata_path = self.index_root / "index.json"
            if not metadata_path.is_file() or metadata_path.stat().st_size > 8_000_000:
                return False
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not (type(metadata.get("schema_version")) is int and metadata.get("schema_version") == 1 and metadata.get("model") == EMBEDDING_MODEL
                    and type(metadata.get("dimensions")) is int and metadata.get("dimensions") == DIMENSIONS and metadata.get("byte_order") == "little"
                    and metadata.get("normalized") is True and metadata.get("repository") == PUBLIC_REPOSITORY):
                raise SemanticIndexError("Incompatible public semantic index")
            if metadata["release_manifest_sha256"] != sha256((self.root / "release-manifest.json").read_bytes()):
                raise SemanticIndexError("Stale public semantic index")
            _, records = verify_public_pack(self.root)
            for row in metadata["files"]:
                if records.get(row["path"], {}).get("sha256") != row["sha256"]:
                    raise SemanticIndexError("Semantic source identity changed")
            docs = {(doc.path, doc.id): (index, doc) for index, doc in enumerate(self.documents)}
            entries = metadata["entries"]
            if not isinstance(entries, list) or not 0 < len(entries) <= MAX_INDEX_VECTORS:
                raise SemanticIndexError("Invalid public semantic entry count")
            hashes, input_hashes, seen, indexes = {}, {}, set(), []
            for row in entries:
                identity = (row["path"], row["document_id"])
                index, doc = docs[identity]
                if not _primary(doc) or type(row["segment"]) is not int or row["segment"] < 0:
                    raise SemanticIndexError("Semantic entry is not a public primary chunk")
                if identity not in hashes:
                    hashes[identity] = document_hash(doc)
                    input_hashes[identity] = [sha256(text.encode("utf-8")) for text in embedding_inputs(doc)]
                if (row["text_sha256"] != hashes[identity] or row["segment"] >= len(input_hashes[identity])
                        or row["input_sha256"] != input_hashes[identity][row["segment"]]):
                    raise SemanticIndexError("Semantic chunk content changed")
                segment_identity = (*identity, row["segment"])
                if segment_identity in seen:
                    raise SemanticIndexError("Duplicate public semantic segment")
                seen.add(segment_identity)
                indexes.append(index)
            filename = metadata["vector_file"]
            # Indexes built before 2026-10-04 carried the checksum in the file name instead.
            if filename != VECTOR_FILE and not (re.fullmatch(r"vectors-[0-9a-f]{64}\.f32", filename)
                                                and filename == "vectors-" + metadata["vector_sha256"] + ".f32"):
                raise SemanticIndexError("Invalid public semantic vector path")
            vector_path = _safe_path(self.index_root, filename)
            size = len(entries) * DIMENSIONS * 4
            if vector_path.stat().st_size != size:
                raise SemanticIndexError("Semantic vector file has an invalid size")
            raw = vector_path.read_bytes()
            if sha256(raw) != metadata["vector_sha256"]:
                raise SemanticIndexError("Semantic vector checksum changed")
            vectors = array("f")
            vectors.frombytes(raw)
            if sys.byteorder != "little":
                vectors.byteswap()
            for offset in range(0, len(vectors), DIMENSIONS):
                norm = sum(value * value for value in vectors[offset:offset + DIMENSIONS])
                if not math.isfinite(norm) or abs(norm - 1) > .002:
                    raise SemanticIndexError("Semantic vectors are not normalized finite values")
            self.vectors, self.document_indexes = vectors, indexes
            self.ready, self.disabled_reason = True, ""
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            self.disabled_reason = "invalid-or-stale-index"
            return False

    def _rank(self, query: array) -> list[dict]:
        scores = {}
        for row, docindex in enumerate(self.document_indexes):
            offset = row * DIMENSIONS
            score = sum(self.vectors[offset + column] * query[column] for column in range(DIMENSIONS))
            scores[docindex] = max(score, scores.get(docindex, -1))
        # A conservative small-sample cutoff reduces unrelated semantic hits;
        # it is not an authority or factual-support probability.
        return [{"docindex": docindex, "score": round(score, 6)} for docindex, score in heapq.nlargest(24, scores.items(), key=lambda row: row[1]) if score >= MIN_SIMILARITY]

    async def candidates(self, client: httpx.AsyncClient, token: str, query: str) -> list[dict]:
        if not self.ready or not token or not query.strip():
            return []
        query = next(_byte_windows(query[:2500].strip(), MAX_INPUT_BYTES), "")
        try:
            async with asyncio.timeout(8):
                vectors, _ = await request_embeddings(client, token, [query], timeout=8)
                return await asyncio.to_thread(self._rank, vectors[0])
        except (SemanticIndexError, TimeoutError):
            return []
