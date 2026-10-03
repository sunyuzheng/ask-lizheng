from __future__ import annotations

import asyncio
import hashlib
import json
from array import array
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scripts import build_semantic_index as builder
from server.semantic import DIMENSIONS, MAX_INPUT_BYTES, SemanticIndex, SemanticIndexError, dry_run_summary, embedding_inputs, normalized_vector, prepare_index, request_embeddings, verify_public_pack


def write_manifest(root):
    files = [{"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
             for path in root.rglob("*") if path.is_file() and path.name != "release-manifest.json"]
    (root / "release-manifest.json").write_text(json.dumps({"repository": "sunyuzheng/lizheng-open-context", "intended_visibility": "public", "snapshot_at": "2026-09-30", "files": files}))


@pytest.fixture
def public_docs(tmp_path):
    root = tmp_path / "data/context"
    folder = root / "corpus/community-posts"
    folder.mkdir(parents=True)
    texts = ["撤掉帮助之后，换一道题可以检验学习迁移。", "长期维护产品，不能仅看原型是否能跑。", "这是 AI 从资料生成的推理导航。", "仅标题，不含正文。"]
    docs = []
    for index, text in enumerate(texts):
        path = folder / f"source-{index}.md"
        path.write_text(text, encoding="utf-8")
        docs.append(SimpleNamespace(id=f"source-{index}#chunk-1", source_id=f"source-{index}", title=f"公开材料{index}", section="", text=text,
                                    path=path.relative_to(root).as_posix(), evidence_role="published-source", content_origin="yuzheng-published-text",
                                    generation_method="not-established", source_type="community-post", source_family=f"family-{index}"))
    docs[2].evidence_role = "secondary-synthesis"
    docs[2].generation_method = "ai-written"
    docs[3].evidence_role = "metadata-only"
    docs[3].source_type = "video-catalog"
    write_manifest(root)
    return root, docs


def index_file(root, docs):
    metadata, inputs = prepare_index(root, docs)
    vectors = array("f", [1.0] + [0.0] * (DIMENSIONS - 1) + [0.0, 1.0] + [0.0] * (DIMENSIONS - 2))
    raw = vectors.tobytes()
    sha = hashlib.sha256(raw).hexdigest()
    metadata.update(vector_file=f"vectors-{sha}.f32", vector_sha256=sha)
    output = root.parent / "semantic"
    output.mkdir()
    (output / metadata["vector_file"]).write_bytes(raw)
    (output / "index.json").write_text(json.dumps(metadata))
    return metadata, output


def test_only_verified_primary_bodies_are_prepared(public_docs):
    root, docs = public_docs
    metadata, inputs = prepare_index(root, docs)
    assert len(inputs) == 2
    assert "撤掉帮助" in inputs[0] and "AI 从资料" not in " ".join(inputs)
    assert len(metadata["files"]) == 2
    summary = dry_run_summary(metadata, inputs)
    assert summary["vector_bytes"] == 2 * DIMENSIONS * 4
    assert summary["estimated_requests"] == 1
    docs[0].text += " Private unpublished memory that is absent from the file."
    with pytest.raises(SemanticIndexError):
        prepare_index(root, docs)


def test_public_mixed_speech_is_indexed_without_author_stance_upgrade(public_docs):
    root, docs = public_docs
    transcript = docs[0]
    transcript.evidence_role = "speaker-attributed-speech"
    transcript.content_origin = "mixed-or-unresolved-speech"
    transcript.yuzheng_stance_weight = "not-evidence"
    transcript.source_type = "video-transcript"
    transcript.source_visibility = "members-only"
    transcript.text_access = "public"
    transcript.speaker_classification = "mixed-or-unresolved"
    metadata, inputs = prepare_index(root, docs)
    assert any(row["document_id"] == transcript.id for row in metadata["entries"])
    assert "撤掉帮助" in inputs[0]
    assert transcript.yuzheng_stance_weight == "not-evidence"
    assert transcript.content_origin == "mixed-or-unresolved-speech"
    assert transcript.evidence_role == "speaker-attributed-speech"


@pytest.mark.parametrize("mutation", ["private", "hash", "hidden", "escape"])
def test_private_unpinned_or_escaped_sources_are_rejected(public_docs, mutation):
    root, _ = public_docs
    manifest_path = root / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "private": manifest["intended_visibility"] = "private"
    if mutation == "hash": manifest["files"][0]["sha256"] = "0" * 64
    if mutation == "hidden": manifest["files"][0]["path"] = ".private/secret.md"
    if mutation == "escape": manifest["files"][0]["path"] = "../private.md"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SemanticIndexError):
        verify_public_pack(root)


def test_long_unicode_source_is_fully_covered_with_bounded_windows(public_docs):
    _, docs = public_docs
    docs[0].text = "公开正文甲乙丙。" * 1500 + "结尾的迁移论点。"
    docs[0].title = "长正文"
    inputs = embedding_inputs(docs[0])
    assert len(inputs) > 1 and all(len(text.encode()) <= MAX_INPUT_BYTES for text in inputs)
    assert "结尾的迁移论点。" in inputs[-1]
    assert sum(len(text.split("\n", 1)[1]) for text in inputs) == len(docs[0].text)


def test_loading_and_rank_uses_chunk_ids_and_normalized_vectors(public_docs):
    root, docs = public_docs
    index_file(root, docs)
    index = SemanticIndex(root, docs)
    assert index.load() and index.ready
    assert index._rank(normalized_vector([1.0] + [0.0] * (DIMENSIONS - 1))) == [{"docindex": 0, "score": 1.0}]
    assert index._rank(normalized_vector([0.0, 1.0] + [0.0] * (DIMENSIONS - 2))) == [{"docindex": 1, "score": 1.0}]
    # Weak matches to both known vectors do not become candidate evidence.
    assert index._rank(normalized_vector([.35, 0, (1 - .35 ** 2) ** .5] + [0] * (DIMENSIONS - 3))) == []


@pytest.mark.parametrize("mutation", ["manifest", "body", "vector", "segment", "normalization", "path"])
def test_stale_or_corrupt_indexes_disable_without_exposing_data(public_docs, mutation):
    root, docs = public_docs
    metadata, output = index_file(root, docs)
    if mutation == "manifest": (root / "release-manifest.json").write_text("{}")
    if mutation == "body": docs[0].text = "Changed document content."
    if mutation == "vector": (output / metadata["vector_file"]).write_bytes(b"bad")
    if mutation == "segment": metadata["entries"][0]["segment"] = 9999
    if mutation == "normalization": metadata["normalized"] = False
    if mutation == "path": metadata["vector_file"] = "../private.f32"
    (output / "index.json").write_text(json.dumps(metadata))
    index = SemanticIndex(root, docs)
    assert not index.load() and not index.ready and not index.vectors


def test_embeddings_response_indexes_are_validated_and_reordered():
    async def run():
        def handler(request):
            assert str(request.url) == "https://space.ai-builders.com/backend/v1/embeddings"
            assert json.loads(request.content) == {"model": "text-embedding-3-small", "input": ["公开一", "公开二"], "dimensions": 256, "encoding_format": "float"}
            return httpx.Response(200, json={"model": "text-embedding-3-small", "data": [{"index": 1, "embedding": [0, 2] + [0] * 254}, {"index": 0, "embedding": [2] + [0] * 255}], "usage": {"total_tokens": 8}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            vectors, tokens = await request_embeddings(client, "synthetic-token", ["公开一", "公开二"], 8)
            assert vectors[0][0] == 1 and vectors[1][1] == 1 and tokens == 8
    asyncio.run(run())


@pytest.mark.parametrize("response", [
    {"model": "other-model", "data": []},
    {"model": "text-embedding-3-small", "data": [{"index": 0, "embedding": [1] * 5}]},
    {"model": "text-embedding-3-small", "data": [{"index": 0, "embedding": [0] * 256}]},
    {"model": "text-embedding-3-small", "data": [{"index": 3, "embedding": [1] * 256}]},
])
def test_invalid_embeddings_are_rejected(response):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as client:
            with pytest.raises(SemanticIndexError):
                await request_embeddings(client, "synthetic-token", ["公开"], 8)
    asyncio.run(run())


def test_runtime_query_has_one_ephemeral_request_and_failure_falls_back(public_docs, capsys):
    root, docs = public_docs
    index_file(root, docs)
    index = SemanticIndex(root, docs)
    index.load()
    calls = []
    async def run():
        def handler(request):
            calls.append(request)
            body = json.loads(request.content)
            assert len(body["input"]) == 1 and len(body["input"][0].encode()) <= MAX_INPUT_BYTES
            if len(calls) == 1:
                return httpx.Response(200, json={"model": "text-embedding-3-small", "data": [{"index": 0, "embedding": [1] + [0] * 255}]})
            return httpx.Response(401, json={"error": "synthetic-token user-question-sensitive"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await index.candidates(client, "synthetic-token", "user-question-sensitive") == [{"docindex": 0, "score": 1.0}]
            assert await index.candidates(client, "synthetic-token", "user-question-sensitive") == []
            assert await index.candidates(client, "", "user-question-sensitive") == []
    asyncio.run(run())
    assert len(calls) == 2 and "user-question-sensitive" not in capsys.readouterr().out
    assert not any("user-question" in path.name for path in root.parent.rglob("*"))


def test_runtime_embedding_timeout_returns_lexical_fallback(public_docs, monkeypatch):
    root, docs = public_docs
    index_file(root, docs)
    index = SemanticIndex(root, docs)
    index.load()
    async def timed_out(*args, **kwargs):
        raise SemanticIndexError("Embedding request failed or timed out")
    monkeypatch.setattr("server.semantic.request_embeddings", timed_out)
    async def run():
        async with httpx.AsyncClient() as client:
            assert await index.candidates(client, "synthetic-token", "公开合成问题") == []
    asyncio.run(run())


def test_builder_default_dry_run_never_reads_credentials_or_uses_network(public_docs, monkeypatch, capsys):
    root, docs = public_docs
    metadata, inputs = prepare_index(root, docs)
    class NoEnvironment(dict):
        def get(self, *args): raise AssertionError("dry-run read credentials")
    async def forbidden(*args): raise AssertionError("dry-run made external requests")
    monkeypatch.setattr(builder, "os", SimpleNamespace(environ=NoEnvironment()))
    monkeypatch.setattr(builder, "plan", lambda: (metadata, inputs))
    monkeypatch.setattr(builder, "build", forbidden)
    assert builder.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry-run" and output["public_primary_chunks"] == 2
    assert not (root.parent / "semantic").exists()


def test_builder_commits_only_public_vectors_and_hash_metadata(public_docs, monkeypatch):
    root, docs = public_docs
    metadata, inputs = prepare_index(root, docs)
    monkeypatch.setattr(builder, "PROJECT", root.parents[1])
    async def synthetic(client, token, batch, timeout):
        assert token == "synthetic-token"
        return [normalized_vector([1.0] + [0.0] * 255) for _ in batch], 10
    monkeypatch.setattr(builder, "request_embeddings", synthetic)
    result = asyncio.run(builder.build(metadata, inputs, "synthetic-token"))
    assert result["usage_tokens"] == 10
    output = root.parent / "semantic"
    persisted = (output / "index.json").read_text()
    assert "synthetic-token" not in persisted and "撤掉帮助" not in persisted
    assert SemanticIndex(root, docs).load()
