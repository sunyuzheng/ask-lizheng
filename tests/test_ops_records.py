"""Synthetic durable ops protocol and admission; no provider or storage calls."""
import asyncio
import hashlib
import hmac
import json
from uuid import UUID, uuid4

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from server.admission import AdmissionError, Principal, QUOTA_STORE_PURPOSE, derived_secret, verify_proof
from server.app import AskRequest, create_app
from server.ops_records import OpsRecorder, OPS_BODY_LIMIT, archived_answer
from server.query_records import QUERY_STORE_HEADER, QUERY_STORE_URL
from server.quota import MemoryQuotaStore
from test_backend import answer_for, context_pack, events
from test_quota import SECRET, enabled, post, proof

TOKEN = "synthetic-ops-provider-token"
VISITOR = "guest:" + "a" * 43
CONVERSATION = "77777777-8888-4999-aaaa-bbbbbbbbbbbb"


@pytest.fixture
def ops(monkeypatch):
    monkeypatch.setenv("ASK_OPS_ENABLED", "true")
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "true")
    monkeypatch.setenv("AI_BUILDER_TOKEN", TOKEN)


def payload(**updates):
    return {"question": "职业选择怎么做", "intent": "apply", "query_log_notice": "v3", "conversation_id": CONVERSATION, **updates}


def identity(**updates):
    return {"updates": {"visitor": VISITOR, "entrypoint": "standalone", **updates}}


def prepared(recorder):
    return recorder.prepare(question="synthetic公开问题", created_at="2026-10-01T10:00:00.000Z", model="deepseek-v4-flash",
        principal=Principal("founding:other-subject", "founding", str(uuid4()), VISITOR, "home"), conversation_id=CONVERSATION, intent="apply")


def test_signed_v3_proof_accepts_pair_and_remains_body_bound():
    body = json.dumps(payload()).encode()
    p = verify_proof(proof(body, **identity()), SECRET, "POST", "/api/ask", body)
    assert p.visitor == VISITOR and p.entrypoint == "standalone"
    with pytest.raises(AdmissionError):
        verify_proof(proof(body, **identity()), SECRET, "POST", "/api/ask", body + b" ")


@pytest.mark.parametrize("updates", [
    {"visitor": VISITOR}, {"entrypoint": "home"}, {"visitor": "founding:identity", "entrypoint": "home"},
    {"visitor": "guest:" + "a" * 42, "entrypoint": "home"}, {"visitor": VISITOR, "entrypoint": "external"},
    {"visitor": VISITOR, "entrypoint": []}, {"visitor": VISITOR, "entrypoint": "home", "extra": 1},
])
def test_signed_invalid_ops_claims_rejected(updates):
    with pytest.raises(AdmissionError):
        verify_proof(proof(updates=updates), SECRET, "POST", "/api/ask", b"")


def test_quota_route_cannot_carry_ops_claims():
    with pytest.raises(AdmissionError):
        verify_proof(proof(method="GET", path="/api/quota", **identity()), SECRET, "GET", "/api/quota", b"")


@pytest.mark.parametrize("update", [{"conversation_id": None}, {"conversation_id": "not-uuid"},
    {"conversation_id": CONVERSATION.upper()}, {"visitor": VISITOR}])
def test_v3_schema_requires_canonical_conversation_and_no_identity(update):
    with pytest.raises(ValueError): AskRequest(**payload(**update))


def test_exact_signing_stable_begin_retry_and_identity_domain_separation(ops):
    requests = []
    async def run():
        def transport(request):
            requests.append(request)
            return httpx.Response(503 if len(requests) == 1 else 200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            recorder = OpsRecorder(client, clock=lambda: 1000)
            record = prepared(recorder)
            await recorder.start(record)
            assert await recorder.finish(record["record_id"], "error", 1234, error_code="request_failed")
    asyncio.run(run())
    assert len(requests) == 3 and requests[0].content == requests[1].content
    key = derived_secret(TOKEN, QUOTA_STORE_PURPOSE).encode()
    begin = json.loads(requests[0].content)
    assert set(begin) == {"v", "event", "record_id", "question", "created_at", "model", "visitor_id", "conversation_id", "intent", "entrypoint"}
    assert str(UUID(begin["record_id"])) == begin["record_id"]
    visitor = hmac.new(key, ("ask-ops:visitor:v3:" + VISITOR).encode(), hashlib.sha256).hexdigest()
    assert begin["visitor_id"] == visitor
    assert begin["conversation_id"] == hmac.new(key, ("ask-ops:conversation:v3:" + visitor + ":" + CONVERSATION).encode(), hashlib.sha256).hexdigest()
    assert "founding:other-subject" not in requests[0].content.decode() and VISITOR not in requests[0].content.decode()
    for r in requests:
        assert str(r.url) == QUERY_STORE_URL and r.headers["content-type"] == "application/octet-stream"
        assert "authorization" not in r.headers and "cookie" not in r.headers
        assert r.content == json.dumps(json.loads(r.content), ensure_ascii=False, separators=(",", ":")).encode()
        version, expiry, signature = r.headers[QUERY_STORE_HEADER].split(".")
        assert version == "v3" and expiry == "1045"
        message = f"ask-ops-store:v3:{expiry}:{hashlib.sha256(r.content).hexdigest()}".encode()
        assert hmac.compare_digest(signature, hmac.new(key, message, hashlib.sha256).hexdigest())
        assert not hmac.compare_digest(signature, hmac.new(key, message.replace(b"ask-ops-store:v3", b"ask-query-store:v1"), hashlib.sha256).hexdigest())
    assert begin["v"] == 3
    assert json.loads(requests[2].content) == {"v": 3, "event": "finish", "record_id": begin["record_id"], "status": "error", "duration_ms": 1234,
        "answer": None, "error_code": "request_failed"}


@pytest.mark.parametrize("response", [httpx.Response(307, headers={"Location": "https://untrusted.example"}),
    httpx.Response(200, json={"ok": False}), httpx.Response(200, json={"ok": 1}), httpx.Response(200, json={"ok": True, "extra": 1}),
    httpx.Response(200, content=b"x" * 4097)])
def test_redirect_or_invalid_ack_is_not_confirmation(ops, response):
    seen = []
    async def run():
        def transport(request): seen.append(str(request.url)); return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
            recorder = OpsRecorder(client)
            with pytest.raises(AdmissionError, match="ops_storage_unavailable"): await recorder.start(prepared(recorder))
    asyncio.run(run())
    assert seen == [QUERY_STORE_URL]


def test_durable_ack_before_model_and_exact_finish_no_legacy_record(context_pack, enabled, ops, monkeypatch):
    stored, generated = [], []
    def transport(request):
        record = json.loads(request.content)
        if record["event"] == "finish": assert app.state.slots._value == 3
        stored.append(record)
        return httpx.Response(200, json={"ok": True})
    async def generate(client, token, model, request, passages, **callbacks):
        assert [r["event"] for r in stored] == ["start"]
        generated.append(request)
        return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    store = MemoryQuotaStore()
    app = create_app(context_pack, quota_store=store, query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        assert client.get("/api/meta").json()["ops_logging"] == {"enabled": True, "retention": "until_deleted", "notice": "v4", "answer_archive": True,
            "context_archive": True, "public_display": "deidentified"}
        response = post(client, payload(context="synthetic private background", history=[{"question": "synthetic history", "summary": "old summary"}]), **identity())
        assert events(response)[-1][1]["status"] == "answered" and app.state.slots._value == 3
        assert [r["event"] for r in stored] == ["start", "finish"] and stored[1]["status"] == "answered"
        assert set(stored[1]) == {"v", "event", "record_id", "status", "duration_ms", "answer", "error_code"}
        final = events(response)[-1][1]
        assert stored[1]["answer"] == {key: value for key, value in final.items() if key != "quota"}
        assert stored[1]["error_code"] is None and stored[1]["answer"]["sources"]
        assert not any(k in generated[0] for k in ("query_log_notice", "conversation_id", "visitor_id", "entrypoint"))
        assert not any(text in json.dumps(stored) for text in ("private background", "synthetic history", "anon:synthetic", "old summary"))


def test_start_failure_never_calls_provider_and_refunds_once(context_pack, enabled, ops, monkeypatch):
    seen, generated = [], []
    def transport(request): seen.append(json.loads(request.content)); return httpx.Response(503)
    async def generate(*args, **kwargs): generated.append(True); pytest.fail("provider called before durable start")
    monkeypatch.setattr("server.app.generate_answer", generate)
    class Store(MemoryQuotaStore):
        releases = 0
        async def finish(self, reservation, commit):
            if not commit: self.releases += 1
            return await super().finish(reservation, commit)
    store = Store()
    app = create_app(context_pack, quota_store=store, query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        response = post(client, payload(), **identity())
        assert response.status_code == 503 and response.json()["code"] == "ops_storage_unavailable"
        assert app.state.slots._value == 3 and store.releases == 1 and not generated
        assert [r["event"] for r in seen] == ["start", "start", "finish", "finish"]
        assert seen[0] == seen[1] and seen[2]["status"] == "error"


def test_unending_storage_start_is_bounded_and_never_generates(context_pack, enabled, ops, monkeypatch):
    import time
    monkeypatch.setattr("server.ops_records.WRITE_SECONDS", .01)
    seen = []
    async def transport(request):
        record = json.loads(request.content); seen.append(record)
        if record["event"] == "start": await asyncio.Event().wait()
        return httpx.Response(200, json={"ok": True})
    async def generate(*args, **kwargs): pytest.fail("unconfirmed question reached provider")
    monkeypatch.setattr("server.app.generate_answer", generate)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        started = time.monotonic()
        response = post(client, payload(), **identity())
        assert time.monotonic() - started < .5
        assert response.status_code == 503 and app.state.slots._value == 3
        assert [r["event"] for r in seen] == ["start", "start", "finish"]


def test_finalize_failure_reports_unconfirmed_archive_preserves_answer_and_refunds(context_pack, enabled, ops, monkeypatch):
    seen = []
    def transport(request):
        record = json.loads(request.content); seen.append(record)
        return httpx.Response(200, json={"ok": True}) if record["event"] == "start" else httpx.Response(503)
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        emitted = events(post(client, payload(), **identity()))
        assert emitted[-1][0] == "error" and emitted[-1][1]["code"] == "answer_archive_failed"
        assert not any(name == "result" for name, _ in emitted)
        assert app.state.slots._value == 3 and [r["event"] for r in seen] == ["start", "finish", "finish", "finish", "finish"]
        assert all(record == seen[1] for record in seen[2:])
        assert seen[1]["status"] == "answered" and seen[1]["answer"]["status"] == "answered"
        assert seen[1]["error_code"] is None
        assert asyncio.run(app.state.quota.store.status(Principal("anon:synthetic", "public", str(uuid4()))))["remaining"] == 3


def test_v3_requires_trusted_metadata_and_valid_config_before_reserve(context_pack, enabled, ops, monkeypatch):
    seen = []
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(lambda request: seen.append(request)))
    with TestClient(app) as client:
        assert post(client, payload()).status_code == 403
        assert not seen and app.state.slots._value == 3
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "false")
    app = create_app(context_pack, quota_store=MemoryQuotaStore())
    with TestClient(app) as client:
        assert client.get("/api/meta").json()["ops_logging"]["enabled"] is False
        assert post(client, payload(), **identity()).json()["code"] == "ops_storage_unavailable"
        assert post(client, {"question": "职业选择"}).status_code == 503


def test_header_disconnect_finishes_cancelled_once_and_refunds(context_pack, enabled, ops):
    from starlette.requests import ClientDisconnect
    stored = []
    def transport(request): stored.append(json.loads(request.content)); return httpx.Response(200, json={"ok": True})
    async def run():
        app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
        body = json.dumps(payload()).encode()
        scope = {"type": "http", "method": "POST", "path": "/api/ask", "raw_path": b"/api/ask", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"x-ask-admission", proof(body, **identity()).encode())],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "scheme": "http", "http_version": "1.1",
            "asgi": {"version": "3.0", "spec_version": "2.4"}}
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()
        async def send(message):
            if message["type"] == "http.response.start": raise OSError("synthetic header disconnect")
        async with app.router.lifespan_context(app):
            with pytest.raises((OSError, ClientDisconnect)): await app(scope, receive, send)
            assert app.state.slots._value == 3
            assert [r["event"] for r in stored] == ["start", "finish"] and stored[-1]["status"] == "cancelled"
            assert stored[-1]["answer"] is None and stored[-1]["error_code"] == "request_cancelled"
            assert (await app.state.quota.store.status(Principal("anon:synthetic", "public", str(uuid4()))))["remaining"] == 3
    asyncio.run(run())


def test_archive_snapshot_only_public_final_fields_is_detached():
    source = {"id": "S1", "title": "synthetic source", "url": "https://example.test/public",
        "date": "2026-10-01", "excerpt": "synthetic public excerpt", "author": "synthetic author",
        "source_type": "article", "reason": "synthetic relevance", "attribution_note": "public",
        "evidence_role": "primary", "public_copy_url": "https://example.test/copy", "timecode": "01:23",
        "source_context": "not a public result field", "identity": "synthetic private identity"}
    result = {**answer_for().model_dump(exclude={"source_reasons"}), "sources": [source],
        "quota": {"subject": "synthetic identity"}, "context": "synthetic background",
        "history": ["synthetic history"], "reasoning_content": "synthetic internal reasoning"}
    snapshot = archived_answer(result)
    assert set(snapshot) == {"status", "summary", "sections", "sources", "followups", "clarifying_questions", "limitations"}
    assert set(snapshot["sources"][0]) == set(source) - {"source_context", "identity"}
    assert not any(value in json.dumps(snapshot) for value in ("synthetic identity", "synthetic background", "synthetic history", "internal reasoning"))
    result["sections"][0]["body"] = "mutated later"
    source["excerpt"] = "mutated later"
    assert snapshot["sections"][0]["body"] != "mutated later" and snapshot["sources"][0]["excerpt"] != "mutated later"


def test_large_complete_source_snapshot_and_body_bound(ops):
    seen = []
    result = {**answer_for().model_dump(exclude={"source_reasons"}),
        "sources": [{"id": f"S{i + 1}", "title": "synthetic", "url": "https://example.test/public",
                     "date": "2026-10-01", "excerpt": "公开材料" * 525, "author": "synthetic", "source_type": "article",
                     "reason": "synthetic", "attribution_note": "public", "evidence_role": "primary"} for i in range(12)]}
    async def run():
        def transport(request): seen.append(request.content); return httpx.Response(200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            recorder = OpsRecorder(client)
            assert await recorder.finish(str(uuid4()), "answered", 500, answer=archived_answer(result))
            assert len(seen[0]) > 16384 and json.loads(seen[0])["answer"]["sources"] == result["sources"]
            assert not await recorder.finish(str(uuid4()), "answered", 500, answer={"oversized": "x" * OPS_BODY_LIMIT})
            assert not await recorder.finish(str(uuid4()), "answered", 500)
            assert len(seen) == 1
    asyncio.run(run())


def test_old_v2_notice_cannot_capture_answers_or_reserve(context_pack, enabled, ops, monkeypatch):
    stored = []
    async def generate(*args, **kwargs): pytest.fail("obsolete notice reached provider")
    monkeypatch.setattr("server.app.generate_answer", generate)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(),
        query_record_transport=httpx.MockTransport(lambda request: stored.append(request)))
    with TestClient(app) as client:
        response = post(client, payload(query_log_notice="v2"), **identity())
        assert response.status_code == 422 and not stored and app.state.slots._value == 3


@pytest.mark.parametrize("notice", [None, "v1"])
def test_legacy_notice_never_archives_answer(context_pack, enabled, ops, monkeypatch, notice):
    stored = []
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    def transport(request): stored.append(json.loads(request.content)); return httpx.Response(200, json={"ok": True})
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        request = {"question": "职业选择怎么做", "query_log_notice": notice}
        assert events(post(client, request))[-1][1]["status"] == "answered"
    assert all(record["v"] == 1 and "answer" not in record and "conversation_id" not in record for record in stored)
    if notice is None: assert not stored


@pytest.mark.parametrize("status", ["answered", "clarify", "unsupported", "sources-only"])
def test_every_public_final_result_is_archived_before_emission(context_pack, enabled, ops, monkeypatch, status):
    from server.answers import ProviderFailure
    stored = []
    async def generate(client, token, model, request, passages, **kwargs):
        if status == "sources-only": raise ProviderFailure("provider_timeout")
        answer = answer_for()
        if status != "answered":
            answer.status = status; answer.sections = []
        return answer
    monkeypatch.setattr("server.app.generate_answer", generate)
    def transport(request): stored.append(json.loads(request.content)); return httpx.Response(200, json={"ok": True})
    async def run():
        app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
        body = json.dumps(payload()).encode()
        scope = {"type": "http", "method": "POST", "path": "/api/ask", "raw_path": b"/api/ask", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"x-ask-admission", proof(body, **identity()).encode())],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "scheme": "http", "http_version": "1.1",
            "asgi": {"version": "3.0", "spec_version": "2.4"}}
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered: delivered = True; return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()
        emitted = []
        async def send(message):
            data = message.get("body", b"")
            if b"event: result" in data:
                assert [record["event"] for record in stored] == ["start", "finish"]
                final = json.loads(data.decode().split("data: ", 1)[1])
                assert stored[-1]["answer"] == {key: value for key, value in final.items() if key != "quota"}
                emitted.append(final)
        async with app.router.lifespan_context(app):
            await app(scope, receive, send)
            assert emitted[0]["status"] == status and app.state.slots._value == 3
            assert stored[-1]["error_code"] == ("provider_timeout" if status == "sources-only" else None)
    asyncio.run(run())


def test_persistent_cancel_during_final_archive_preserves_full_answer_and_refunds(context_pack, enabled, ops, monkeypatch):
    monkeypatch.setattr("server.ops_records.WRITE_SECONDS", .01)
    seen, archiving = [], asyncio.Event()
    async def run():
        async def transport(request):
            record = json.loads(request.content); seen.append(record)
            if record["event"] == "finish" and len(seen) == 2:
                archiving.set(); await asyncio.Event().wait()
            await anyio.sleep(0)
            return httpx.Response(200, json={"ok": True})
        async def generate(*args, **kwargs): return answer_for()
        monkeypatch.setattr("server.app.generate_answer", generate)
        app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
        body = json.dumps(payload()).encode()
        scope = {"type": "http", "method": "POST", "path": "/api/ask", "raw_path": b"/api/ask", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"x-ask-admission", proof(body, **identity()).encode())],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "scheme": "http", "http_version": "1.1",
            "asgi": {"version": "3.0", "spec_version": "2.4"}}
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered: delivered = True; return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()
        async def send(message): pass
        async with app.router.lifespan_context(app):
            async with anyio.create_task_group() as group:
                group.start_soon(app, scope, receive, send)
                await archiving.wait()
                assert app.state.slots._value == 3
                group.cancel_scope.cancel()
            assert seen[1] == seen[2] and seen[1]["answer"]["status"] == "answered"
            assert seen[1]["error_code"] is None and app.state.slots._value == 3
            assert (await app.state.quota.store.status(Principal("anon:synthetic", "public", str(uuid4()))))["remaining"] == 3
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["begin", "generation"])
def test_persistent_anyio_cancel_refunds_and_finishes(context_pack, enabled, ops, monkeypatch, phase):
    seen, started = [], asyncio.Event()
    generated_cancelled = []
    async def run():
        async def transport(request):
            record = json.loads(request.content); seen.append(record)
            if record["event"] == "start" and phase == "begin":
                started.set(); await asyncio.Event().wait()
            await anyio.sleep(0)  # Real cancellation checkpoint inside shielded finalize.
            return httpx.Response(200, json={"ok": True})
        async def generate(*args, **kwargs):
            started.set()
            try: await asyncio.Event().wait()
            finally: generated_cancelled.append(True)
        monkeypatch.setattr("server.app.generate_answer", generate)
        app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
        body = json.dumps(payload()).encode()
        scope = {"type": "http", "method": "POST", "path": "/api/ask", "raw_path": b"/api/ask", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"x-ask-admission", proof(body, **identity()).encode())],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "scheme": "http", "http_version": "1.1",
            "asgi": {"version": "3.0", "spec_version": "2.4"}}
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered: delivered = True; return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()
        async def send(message): pass
        async with app.router.lifespan_context(app):
            async with anyio.create_task_group() as group:
                group.start_soon(app, scope, receive, send)
                await started.wait()
                group.cancel_scope.cancel()
            assert app.state.slots._value == 3
            assert [r["event"] for r in seen] == ["start", "finish"] and seen[-1]["status"] == "cancelled"
            assert seen[-1]["answer"] is None and seen[-1]["error_code"] == "request_cancelled"
            assert (await app.state.quota.store.status(Principal("anon:synthetic", "public", str(uuid4()))))["remaining"] == 3
            assert generated_cancelled == ([True] if phase == "generation" else [])
    asyncio.run(run())


@pytest.mark.parametrize("request_updates,background,context", [
    ({}, "0", ""),
    ({"context": "  目前的情况与限制：synthetic处境  "}, "1", "目前的情况与限制：synthetic处境"),
    ({"history": [{"question": "synthetic earlier", "summary": "synthetic summary"}]}, "1", ""),
])
def test_v4_start_marks_notice_background_and_keeps_situation_for_owner(context_pack, enabled, ops, monkeypatch, request_updates, background, context):
    stored = []
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    def transport(request): stored.append(json.loads(request.content)); return httpx.Response(200, json={"ok": True})
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        response = post(client, payload(query_log_notice="v4", intent="understand", **request_updates), **identity())
        assert events(response)[-1][1]["status"] == "answered"
    start, finish = stored
    assert (start["notice_version"], start["has_background"], start["context"]) == ("v4", background, context)
    assert set(finish) == {"v", "event", "record_id", "status", "duration_ms", "answer", "error_code"}
    # History is never stored, only counted as background.
    assert "synthetic earlier" not in json.dumps(stored, ensure_ascii=False)


def test_v3_start_keeps_its_original_shape(context_pack, enabled, ops, monkeypatch):
    stored = []
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    def transport(request): stored.append(json.loads(request.content)); return httpx.Response(200, json={"ok": True})
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        post(client, payload(context="synthetic private background"), **identity())
    assert set(stored[0]) == {"v", "event", "record_id", "question", "created_at", "model", "visitor_id", "conversation_id", "intent", "entrypoint"}
    assert "synthetic private background" not in json.dumps(stored, ensure_ascii=False)


def test_v4_requires_a_conversation():
    with pytest.raises(ValueError): AskRequest(**payload(query_log_notice="v4", conversation_id=None))
