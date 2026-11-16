"""Optional diagnostics never displace a durable answer. Synthetic HTTP only."""
import asyncio
import copy
import hashlib
import hmac
import json

import httpx
import pytest

from server.ops_records import OPS_BODY_LIMIT, OpsRecorder
from server.query_records import QUERY_STORE_HEADER, QUERY_STORE_URL
from server.retrieval_trace import normalize_trace

RECORD_ID = "01234567-89ab-4cde-8f01-23456789abcd"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def trace():
    return {"v": 1, "context_commit": "1" * 40, "context_release": "2" * 64, "engine_sha256": "3" * 64,
            "steps": [{"stage": "hybrid", "elapsed_ms": 1, "items": [
                {"source_id": "a" * 240, "fragment_id": "b" * 240, "rank": n + 1}
                for n in range(10)]} for _ in range(2)], "gaps": []}


def answer(*, large=False):
    # Every source field remains within HOME/Ops' archivedAnswer limits; twelve
    # source snapshots are legal even when a section uses only one of them.
    sources = [{"id": f"S{i + 1}", "title": "t" * 1000,
                "url": "https://example.org/" + "u" * 1980,
                "public_copy_url": "https://example.org/" + "p" * 1980,
                "excerpt": "x" * 2600, "author": "a" * 400, "attribution_note": "n" * 1000,
                "participant_names": "a" * 4096, "participant_entity_ids": "b" * 4096,
                "participant_aliases": "c" * 4096} for i in range(12)] if large else [
                    {"id": "S1", "title": "synthetic source", "url": "https://example.org/synthetic"}]
    return {"status": "answered", "summary": "synthetic", "sections": [
        {"heading": "synthetic", "body": "synthetic", "source_ids": ["S1"], "kind": "source"}],
        "sources": sources, "followups": [], "clarifying_questions": [], "limitations": ""}


@pytest.fixture(autouse=True)
def synthetic_config(monkeypatch):
    monkeypatch.setenv("ASK_OPS_ENABLED", "true")
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-only-provider-token")


def test_legal_large_answer_drops_trace_before_signing_and_lost_ack_retry():
    captured, stored = [], {}
    result, diagnostic = answer(large=True), trace()
    original = copy.deepcopy((result, diagnostic))
    assert normalize_trace(diagnostic) == diagnostic
    sample = {"v": 3, "event": "finish", "record_id": RECORD_ID, "status": "answered",
              "duration_ms": 1234, "answer": result, "error_code": None, "share_word": "synthetic"}
    assert len(compact(sample)) <= OPS_BODY_LIMIT < len(compact({**sample, "retrieval_trace": diagnostic}))

    async def run():
        def transport(request):
            captured.append(request)
            value = json.loads(request.content)
            assert len(request.content) <= OPS_BODY_LIMIT
            stored.setdefault(value["record_id"], request.content)
            if len(captured) == 1:
                raise httpx.ReadTimeout("synthetic lost acknowledgement", request=request)
            return httpx.Response(200, json={"ok": True})
        clocks = iter((1790870400, 1790870401))
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            recorder = OpsRecorder(client, clock=lambda: next(clocks))
            ack = await recorder.finish(RECORD_ID, "answered", 1234, answer=result,
                                        page_word="synthetic", retrieval_trace=diagnostic)
            return recorder.secret, ack

    secret, ack = asyncio.run(run())
    assert ack == {"ok": True} and len(captured) == 2 and len(stored) == 1
    assert captured[0].content == captured[1].content == compact(sample)
    assert json.loads(captured[0].content)["answer"] == result
    assert (result, diagnostic) == original
    for request in captured:
        assert str(request.url) == QUERY_STORE_URL
        assert request.headers["content-type"] == "application/octet-stream"
        version, expiry, signature = request.headers[QUERY_STORE_HEADER].split(".")
        assert version == "v3"
        message = f"ask-ops-store:v3:{expiry}:{hashlib.sha256(request.content).hexdigest()}"
        assert hmac.compare_digest(signature, hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest())


@pytest.mark.parametrize("diagnostic", [None, {"question": "must not be persisted"},
    {**trace(), "context": "must not be persisted"},
    {**trace(), "steps": [{"stage": "cited", "elapsed_ms": 0, "items": [
        {"source_id": "https://private.example", "fragment_id": "source", "rank": 1}]}]}])
def test_missing_or_invalid_trace_is_omitted_without_failing_answer(diagnostic):
    captured = []
    async def run():
        def transport(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await OpsRecorder(client).finish(RECORD_ID, "answered", 1, answer=answer(), retrieval_trace=diagnostic)
    assert asyncio.run(run()) == {"ok": True}
    assert captured[0]["answer"] == answer() and "retrieval_trace" not in captured[0]


def test_trace_that_fits_is_retained_and_required_answer_overflow_still_fails_closed():
    captured = []
    async def run():
        def transport(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            recorder = OpsRecorder(client)
            assert await recorder.finish(RECORD_ID, "answered", 1, answer=answer(), retrieval_trace=trace()) == {"ok": True}
            invalid = {**answer(), "summary": "x" * OPS_BODY_LIMIT}
            assert await recorder.finish(RECORD_ID, "answered", 1, answer=invalid, retrieval_trace=trace()) is None
    asyncio.run(run())
    assert len(captured) == 1 and captured[0]["retrieval_trace"] == trace()
