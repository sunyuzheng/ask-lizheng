"""Synthetic question recording, with no real provider or storage calls."""
import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from server.admission import QUOTA_STORE_PURPOSE, derived_secret
from server.app import create_app
from server.query_records import QUERY_STORE_HEADER, QUERY_STORE_URL, QueryRecorder
from server.quota import MemoryQuotaStore
from test_backend import answer_for, context_pack, events
from test_quota import enabled, post, proof

TOKEN = "synthetic-query-provider-token"


def record(recorder):
    recorder.enqueue(question="synthetic question", created_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        model="deepseek-v4-flash", status="answered", duration_ms=1234)


@pytest.fixture
def recording(monkeypatch):
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "true")
    monkeypatch.setenv("AI_BUILDER_TOKEN", TOKEN)


@pytest.mark.parametrize("flag,token", [("false", TOKEN), ("", TOKEN), ("true", "")])
def test_disabled_or_missing_key_makes_no_requests(monkeypatch, flag, token):
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", flag)
    monkeypatch.setenv("AI_BUILDER_TOKEN", token)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("disabled recorder sent a request"))) as client:
            recorder = QueryRecorder(client)
            record(recorder)
            assert not recorder.tasks
            await recorder.close()
    asyncio.run(run())


def test_signed_question_only_compact_body_and_idempotent_retry(recording):
    requests = []
    now = time.time()
    async def run():
        def transport(request):
            requests.append(request)
            return httpx.Response(503 if len(requests) == 1 else 200)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            recorder = QueryRecorder(client, clock=lambda: now)
            record(recorder)
            await asyncio.gather(*recorder.tasks)
            await recorder.close()
    asyncio.run(run())
    assert len(requests) == 2 and requests[0].content == requests[1].content
    for request in requests:
        assert str(request.url) == QUERY_STORE_URL and request.headers["content-type"] == "application/octet-stream"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        payload = json.loads(request.content)
        assert set(payload) == {"v", "record_id", "question", "created_at", "model", "status", "duration_ms"}
        assert request.content == json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        version, expiry, signature = request.headers[QUERY_STORE_HEADER].split(".")
        assert version == "v1" and int(expiry) == int(now) + 45
        key = derived_secret(TOKEN, QUOTA_STORE_PURPOSE).encode()
        message = f"ask-query-store:v1:{expiry}:{hashlib.sha256(request.content).hexdigest()}".encode()
        assert hmac.compare_digest(signature, hmac.new(key, message, hashlib.sha256).hexdigest())
        wrong_prefix = message.replace(b"ask-query-store", b"ask-quota-store")
        assert not hmac.compare_digest(signature, hmac.new(key, wrong_prefix, hashlib.sha256).hexdigest())


def test_redirect_is_not_followed_or_retried(recording):
    seen = []
    async def run():
        def transport(request):
            seen.append(str(request.url))
            return httpx.Response(307, headers={"Location": "https://elsewhere.example/collect"}, text="synthetic private error")
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
            recorder = QueryRecorder(client)
            record(recorder)
            await asyncio.gather(*recorder.tasks)
            await recorder.close()
    asyncio.run(run())
    assert seen == [QUERY_STORE_URL]


def test_bounded_queue_close_cancels_pending_without_slot_dependence(recording, monkeypatch):
    monkeypatch.setattr("server.query_records.MAX_PENDING", 2)
    entered, cancelled = [], []
    async def run():
        async def transport(request):
            entered.append(True)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            recorder = QueryRecorder(client)
            for _ in range(20): record(recorder)
            assert len(recorder.tasks) == 2
            await asyncio.sleep(0)
            await asyncio.wait_for(recorder.close(), .1)
            assert not recorder.tasks
            record(recorder)
            assert not recorder.tasks
    asyncio.run(run())
    assert len(entered) == len(cancelled) == 2


def test_valid_admitted_notice_records_without_background_identity_or_history(context_pack, enabled, recording, monkeypatch):
    stored, generated = [], []
    async def generate(client, token, model, payload, passages, **callbacks):
        generated.append(payload)
        return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    def transport(request):
        stored.append(json.loads(request.content))
        return httpx.Response(200)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        assert client.get("/api/meta").json()["query_logging"] == {"enabled": True, "retention_days": 30}
        response = post(client, {"question": "  职业选择怎么做  ", "context": "private synthetic background", "query_log_notice": "v1",
            "history": [{"question": "synthetic history", "summary": "synthetic history summary"}]})
        assert events(response)[-1][1]["status"] == "answered" and app.state.slots._value == 3
        # Give the detached task one loop turn, without model/storage polling.
        client.get("/health")
        assert len(stored) == 1 and stored[0]["question"] == "职业选择怎么做"
        assert stored[0]["status"] == "answered" and stored[0]["duration_ms"] >= 0
        assert "query_log_notice" not in generated[0]
        assert not any(word in json.dumps(stored) for word in ["private synthetic", "synthetic history", "anon:", "email", "context"])


def test_missing_notice_and_rejected_inputs_are_not_recorded(context_pack, enabled, recording, monkeypatch):
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    seen = []
    def transport(request): seen.append(request); return httpx.Response(200)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        assert post(client, {"question": "职业选择怎么做"}).status_code == 200
        assert post(client, {"question": " ", "query_log_notice": "v1"}).status_code == 422
        assert client.post("/api/ask", json={"question": "职业选择", "query_log_notice": "v1"}).status_code == 403
        client.get("/health")
        assert not seen


def test_storage_hang_or_enqueue_failure_cannot_delay_result_or_release(context_pack, enabled, recording, monkeypatch):
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    entered, cancelled = [], []
    async def transport(request):
        entered.append(True)
        try: await asyncio.Event().wait()
        finally: cancelled.append(True)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        start = time.monotonic()
        response = post(client, {"question": "职业选择", "query_log_notice": "v1"})
        assert time.monotonic() - start < 1 and events(response)[-1][1]["status"] == "answered"
        assert app.state.slots._value == 3
        def broken(**kwargs): raise RuntimeError("synthetic private recorder error")
        monkeypatch.setattr(app.state.query_records, "enqueue", broken)
        assert post(client, {"question": "职业选择", "query_log_notice": "v1"}).status_code == 200
        assert app.state.slots._value == 3
    assert entered and cancelled


def test_header_disconnect_records_cancelled_once_without_leaking_active_slot(context_pack, enabled, recording):
    from starlette.requests import ClientDisconnect
    stored = []
    def transport(request):
        stored.append(json.loads(request.content))
        return httpx.Response(200)
    async def run():
        app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
        body = b'{"question":"synthetic admitted question","query_log_notice":"v1"}'
        scope = {"type": "http", "method": "POST", "path": "/api/ask", "raw_path": b"/api/ask", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"x-ask-admission", proof(body).encode())],
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
            with pytest.raises((OSError, ClientDisconnect)):
                await app(scope, receive, send)
            assert app.state.slots._value == 3
            await asyncio.sleep(0)
            assert len(stored) == 1 and stored[0]["status"] == "cancelled"
            assert stored[0]["question"] == "synthetic admitted question"
    asyncio.run(run())


def test_query_recording_requires_admission_mode_even_with_notice(context_pack, recording, monkeypatch):
    monkeypatch.setenv("ASK_QUOTA_ENABLED", "false")
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    seen = []
    def transport(request): seen.append(request); return httpx.Response(200)
    app = create_app(context_pack, query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        assert client.get("/api/meta").json()["query_logging"]["enabled"] is False
        assert client.post("/api/ask", json={"question": "职业选择", "query_log_notice": "v1"}).status_code == 200
        client.get("/health")
        assert not seen


def test_exhausted_and_invalid_notice_requests_never_enter_the_writer(context_pack, enabled, recording, monkeypatch):
    async def generate(*args, **kwargs): return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    seen = []
    def transport(request): seen.append(json.loads(request.content)); return httpx.Response(200)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        assert post(client, {"question": "职业选择", "query_log_notice": "v2"}).status_code == 422
        for _ in range(3):
            assert post(client, {"question": "职业选择", "query_log_notice": "v1"}).status_code == 200
        assert post(client, {"question": "职业选择", "query_log_notice": "v1"}).status_code == 429
        client.get("/health")
        assert len(seen) == 3 and all(record["status"] == "answered" for record in seen)


def test_failed_generation_is_refunded_and_only_final_status_is_recorded(context_pack, enabled, recording, monkeypatch):
    from server.answers import ProviderFailure
    async def generate(*args, **kwargs): raise ProviderFailure("provider_timeout")
    monkeypatch.setattr("server.app.generate_answer", generate)
    seen = []
    def transport(request): seen.append(json.loads(request.content)); return httpx.Response(200)
    app = create_app(context_pack, quota_store=MemoryQuotaStore(), query_record_transport=httpx.MockTransport(transport))
    with TestClient(app) as client:
        response = post(client, {"question": "职业选择", "query_log_notice": "v1"})
        result = events(response)[-1][1]
        assert result["status"] == "sources-only" and result["quota"]["used"] == 0
        client.get("/health")
        assert len(seen) == 1 and seen[0]["status"] == "sources-only"
        assert "failure_code" not in seen[0] and "summary" not in seen[0]
