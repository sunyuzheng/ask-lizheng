"""Cross-topic evidence reading and private receipts; synthetic, no network."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.context_reader import ContextReader, merge_passages
from server.evidence_selection import EvidenceSelection
from server.retrieval import ContextIndex
from server.retrieval_trace import normalize_trace
from server.quota import MemoryQuotaStore
from test_backend import context_pack, index, answer_for, source_file, events
from test_ops_records import ops, payload, identity
from test_quota import enabled, post


def reader(index, **kwargs):
    index.source_revision = "a" * 40
    return ContextReader(index, "职业选择能力作品", intent="understand", **kwargs)


def test_navigation_retains_every_global_window_and_ids_are_stable(index):
    r = reader(index, allow_supplement=True)
    primary = r.retrieve(initial=True)
    merged = r.supplement(r.question, primary)
    assert [p.evidence["document_id"] for p in merged[:len(primary)]] == [p.evidence["document_id"] for p in primary]
    assert all(p.source["fragment_id"] for p in merged)
    assert len(merged) <= 16
    result = {"sources": [{**merged[-1].source, "id": "S1"}]}
    trace = r.cited(result, merged)
    assert trace["steps"][-1]["items"][0]["fragment_id"] == merged[-1].source["fragment_id"]
    assert trace["context_commit"] == "a" * 40


def test_source_routes_cannot_admit_unknown_speakers_or_metadata(context_pack):
    for name, metadata, text in [
        ("unresolved", {"source_type": "video-transcript", "evidence_role": "primary-speech",
                        "content_origin": "mixed-speaker-source", "speaker_id": "unknown", "speaker_status": "unresolved"},
         "合成机器人评测：这句话尚未确认归属。"),
        ("missing", {"source_type": "video-catalog", "evidence_role": "metadata-only"}, "# 合成机器人评测")]:
        source_file(context_pack, "corpus/videos", name + ".md", {
            "id": "synthetic-" + name, "title": "合成机器人评测", "source_family": "family-" + name,
            "source_url": "https://example.org/" + name, **metadata}, text)
    x = ContextIndex(context_pack); x.load(); x.source_revision = "a" * 40
    r = ContextReader(x, "合成机器人评测", intent="understand")
    found = r.supplement(r.question, r.retrieve(initial=True))
    assert not any(p.evidence["source_family"] in {"family-unresolved", "family-missing"} for p in found)
    assert {g["reason"] for g in r.gaps} >= {"speaker_unresolved", "missing_text"}
    assert "尚未确认归属" not in json.dumps(r.trace.snapshot(), ensure_ascii=False)
    assert x.retrieve("合成机器人评测", intent="find", source_families={"family-unresolved"})
    assert x.retrieve("合成机器人评测", intent="understand", source_families={"family-unresolved"}) == []


@pytest.mark.evidence_selection
def test_one_supplement_recovers_another_topic_without_dropping_selected(index):
    r = reader(index, allow_supplement=True)
    first = r.retrieve(initial=True)[:1]
    expected = next(p for p in index.retrieve("如何积累能力", intent="understand")
                    if p.evidence["source_family"] == "video-family")
    calls = []
    async def semantic(*args):
        calls.append(args[-1]); return []
    async def run():
        def provider(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "source_ids": [first[0].source["id"]], "search_query": "如何积累能力"})}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            request = {"question": r.question}
            result = await r.select(client, "synthetic", "gpt-5", request, first,
                                    SimpleNamespace(candidates=semantic), time.monotonic()+30)
            assert "retrieval_gaps" not in request  # Eligibility receipts are not evidence.
            return result
    # The separate source is the missing facet, independently selected here.
    first = [p for p in index.retrieve("职业选择能力作品", intent="understand") if p.evidence["source_family"] != "video-family"][:1]
    found = asyncio.run(run())
    assert len(calls) == 1
    assert found[0].evidence["document_id"] == first[0].evidence["document_id"]
    assert expected.evidence["document_id"] in {p.evidence["document_id"] for p in found}
    assert r.supplement("another query", found) is found
    assert "如何积累能力" not in json.dumps(r.trace.snapshot(), ensure_ascii=False)


def test_supplement_skipped_when_answer_deadline_is_near(index, monkeypatch):
    r = reader(index, allow_supplement=True)
    primary = r.retrieve(initial=True)
    async def plan(*args, **kwargs): return EvidenceSelection(primary, "missing subject")
    async def network(*args): pytest.fail("deadline must prevent extra work")
    monkeypatch.setattr("server.evidence_selection.plan_evidence", plan)
    result = asyncio.run(r.select(None, "", "gpt-5", {}, primary,
        SimpleNamespace(candidates=network), time.monotonic()+2))
    assert result is primary and not r.supplemented


def test_stable_path_never_runs_experimental_search_or_sends_gap_metadata(index, monkeypatch):
    r = reader(index)
    primary = r.retrieve(initial=True)
    async def plan(*args, **kwargs):
        assert kwargs["allow_supplement"] is False
        return EvidenceSelection(primary, "do not execute")
    async def network(*args): pytest.fail("production must not activate the unproven prototype")
    monkeypatch.setattr("server.evidence_selection.plan_evidence", plan)
    request = {"question": r.question}
    found = asyncio.run(r.select(None, "", "gpt-5", request, primary,
        SimpleNamespace(candidates=network), time.monotonic()+30))
    assert found is primary and "retrieval_gaps" not in request
    assert not r.supplemented


def test_cross_read_merge_keeps_the_archive_source_ceiling(index):
    from dataclasses import replace
    from server.context_reader import MAX_PASSAGES
    p = index.retrieve("职业选择")[0]
    rows = [replace(p, evidence={**p.evidence, "source_family":f"family-{n}"}) for n in range(16)]
    assert len(merge_passages(rows[:8], rows[8:])) == MAX_PASSAGES == 12


@pytest.mark.parametrize("change", [
    lambda t: t.update(question="private"),
    lambda t: t["steps"][0]["items"][0].update(fragment_id="/Users/private.txt"),
    lambda t: t["steps"][0]["items"][0].update(reason={}),
    lambda t: t["steps"][0].update(stage=[]),
    lambda t: t["steps"][0].update(elapsed_ms=True),
    lambda t: t["gaps"].append({"source_id":"source", "reason":[]}),
    lambda t: t.update(context_commit="main"),
])
def test_trace_rejects_private_free_text_and_malformed_values(index, change):
    r = reader(index); r.retrieve(initial=True)
    value = copy.deepcopy(r.trace.snapshot())
    assert value
    change(value)
    assert normalize_trace(value) is None


def test_archive_trace_is_private_and_bound_to_final_sources(context_pack, enabled, ops, monkeypatch):
    stored = []
    def transport(request):
        stored.append(json.loads(request.content)); return httpx.Response(200, json={"ok": True})
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        app.state.index.source_revision = "a" * 40
        final = events(post(client, payload(context="synthetic private situation"), **identity()))[-1][1]
    trace = stored[-1]["retrieval_trace"]
    assert final["status"] == "answered"
    assert trace["steps"][-1]["stage"] == "cited"
    assert {i["fragment_id"] for i in trace["steps"][-1]["items"]} == {s["fragment_id"] for s in final["sources"]}
    assert "retrieval_trace" not in final and "retrieval_trace" not in stored[-1]["answer"]
    assert "synthetic private situation" not in json.dumps(stored)
