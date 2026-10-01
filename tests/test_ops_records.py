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
from server.ops_records import OpsRecorder
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
    return {"question": "职业选择怎么做", "intent": "apply", "query_log_notice": "v2", "conversation_id": CONVERSATION, **updates}


def identity(**updates):
    return {"updates": {"visitor": VISITOR, "entrypoint": "standalone", **updates}}


def prepared(recorder):
    return recorder.prepare(question="synthetic公开问题", created_at="2026-10-01T10:00:00.000Z", model="deepseek-v4-flash",
        principal=Principal("founding:other-subject", "founding", str(uuid4()), VISITOR, "home"), conversation_id=CONVERSATION, intent="apply")


def test_signed_v2_proof_accepts_pair_and_remains_body_bound():
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
def test_v2_schema_requires_canonical_conversation_and_no_identity(update):
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
            assert await recorder.finish(record["record_id"], "answered", 1234)
    asyncio.run(run())
    assert len(requests) == 3 and requests[0].content == requests[1].content
    key = derived_secret(TOKEN, QUOTA_STORE_PURPOSE).encode()
    begin = json.loads(requests[0].content)
    assert set(begin) == {"v", "event", "record_id", "question", "created_at", "model", "visitor_id", "conversation_id", "intent", "entrypoint"}
    assert str(UUID(begin["record_id"])) == begin["record_id"]
    visitor = hmac.new(key, ("ask-ops:visitor:v2:" + VISITOR).encode(), hashlib.sha256).hexdigest()
    assert begin["visitor_id"] == visitor
    assert begin["conversation_id"] == hmac.new(key, ("ask-ops:conversation:v2:" + visitor + ":" + CONVERSATION).encode(), hashlib.sha256).hexdigest()
    assert "founding:other-subject" not in requests[0].content.decode() and VISITOR not in requests[0].content.decode()
    for r in requests:
        assert str(r.url) == QUERY_STORE_URL and r.headers["content-type"] == "application/octet-stream"
        assert "authorization" not in r.headers and "cookie" not in r.headers
        assert r.content == json.dumps(json.loads(r.content), ensure_ascii=False, separators=(",", ":")).encode()
        version, expiry, signature = r.headers[QUERY_STORE_HEADER].split(".")
        assert version == "v2" and expiry == "1045"
        message = f"ask-ops-store:v2:{expiry}:{hashlib.sha256(r.content).hexdigest()}".encode()
        assert hmac.compare_digest(signature, hmac.new(key, message, hashlib.sha256).hexdigest())
        assert not hmac.compare_digest(signature, hmac.new(key, message.replace(b"ask-ops-store:v2", b"ask-query-store:v1"), hashlib.sha256).hexdigest())
    assert json.loads(requests[2].content) == {"v": 2, "event": "finish", "record_id": begin["record_id"], "status": "answered", "duration_ms": 1234}


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
    def transport(request): stored.append(json.loads(request.content)); return httpx.Response(200, json={"ok": True})
    async def generate(client, token, model, request, passages, **callbacks):
        assert [r["event"] for r in stored] == ["start"]
        generated.append(request)
        return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    store = MemoryQuotaStore()
    app = create_app(context_pack, quota_store=store, query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        assert client.get("/api/meta").json()["ops_logging"] == {"enabled": True, "retention": "until_deleted"}
        response = post(client, payload(context="synthetic private background", history=[{"question": "synthetic history", "summary": "old summary"}]), **identity())
        assert events(response)[-1][1]["status"] == "answered" and app.state.slots._value == 3
        assert [r["event"] for r in stored] == ["start", "finish"] and stored[1]["status"] == "answered"
        assert set(stored[1]) == {"v", "event", "record_id", "status", "duration_ms"}
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


def test_finalize_failure_preserves_answer_and_slot(context_pack, enabled, ops, monkeypatch):
    seen = []
    def transport(request):
        record = json.loads(request.content); seen.append(record)
        return httpx.Response(200, json={"ok": True}) if record["event"] == "start" else httpx.Response(503)
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        assert events(post(client, payload(), **identity()))[-1][1]["status"] == "answered"
        assert app.state.slots._value == 3 and [r["event"] for r in seen] == ["start", "finish", "finish"]


def test_v2_requires_trusted_metadata_and_valid_config_before_reserve(context_pack, enabled, ops, monkeypatch):
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
            assert (await app.state.quota.store.status(Principal("anon:synthetic", "public", str(uuid4()))))["remaining"] == 3
            assert generated_cancelled == ([True] if phase == "generation" else [])
    asyncio.run(run())
