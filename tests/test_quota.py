"""Admission and completed-answer quotas, using synthetic identity metadata only."""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from server.admission import AdmissionError, HEADER, Principal, verify_proof
from server.app import AskRequest, create_app
from server.quota import LEASE_SECONDS, MemoryQuotaStore, QuotaAdmission, RedisQuotaStore, Reservation, day_window
from test_backend import answer_for, context_pack, events

SECRET = "synthetic-admission-secret-not-live" * 2


def proof(body=b"", method="POST", path="/api/ask", *, subject="anon:synthetic", tier="public", attempt=None, now=None, updates=None):
    claims = {"v": 1, "sub": subject, "tier": tier, "attempt": attempt or str(uuid4()), "exp": int(now or time.time()) + 60,
              "method": method, "path": path, "body_sha256": hashlib.sha256(body).hexdigest()}
    claims.update(updates or {})
    encoded = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), ("v1." + encoded).encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return "v1." + encoded + "." + signature


def post(client, payload=None, **identity):
    body = json.dumps(payload or {"question": "职业选择怎么做"}, ensure_ascii=False).encode()
    return client.post("/api/ask", content=body, headers={HEADER: proof(body, **identity), "Content-Type": "application/json"})


def get_quota(client, **identity):
    return client.get("/api/quota", headers={HEADER: proof(method="GET", path="/api/quota", **identity)})


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ASK_QUOTA_ENABLED", "true")
    monkeypatch.setenv("ASK_ADMISSION_SECRET", SECRET)
    monkeypatch.setenv("ASK_RATE_PER_MINUTE", "100")
    monkeypatch.delenv("ASK_QUOTA_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("ASK_QUOTA_REDIS_REST_TOKEN", raising=False)


def test_versioned_proof_exact_body_destination_and_expiry():
    body = b'{"question":"synthetic"}'
    principal = verify_proof(proof(body, now=1000), SECRET, "POST", "/api/ask", body, 1000)
    assert principal.subject == "anon:synthetic" and principal.tier == "public"
    for kwargs in [{"body": body + b" "}, {"method": "GET"}, {"path": "/api/quota"}, {"now": 1060}, {"now": 900}]:
        arguments = {"method": "POST", "path": "/api/ask", "body": body, "now": 1000, **kwargs}
        with pytest.raises(AdmissionError) as error:
            verify_proof(proof(body, now=1000), SECRET, **arguments)
        assert error.value.code == "invalid_admission"


@pytest.mark.parametrize("updates", [{"v": True}, {"tier": "stay"}, {"tier": ["founding"]}, {"exp": True}, {"exp": 1300},
                                     {"sub": "email@example.org"}, {"attempt": "not-a-uuid"}, {"extra": True}, {"body_sha256": "wrong"}])
def test_untrusted_claims_do_not_expand_entitlements(updates):
    with pytest.raises(AdmissionError):
        verify_proof(proof(now=1000, updates=updates), SECRET, "POST", "/api/ask", b"", 1000)


def test_proof_forgery_missing_and_weak_key_fail_closed():
    valid = proof()
    with pytest.raises(AdmissionError, match="invalid_admission"):
        verify_proof(valid[:-2] + "zz", SECRET, "POST", "/api/ask", b"")
    with pytest.raises(AdmissionError, match="admission_required"):
        verify_proof(None, SECRET, "POST", "/api/ask", b"")
    with pytest.raises(AdmissionError, match="quota_unavailable"):
        verify_proof(valid, "short", "POST", "/api/ask", b"")


def test_duplicate_signed_claims_are_rejected():
    claims = '{"v":1,"v":1}'
    encoded = base64.urlsafe_b64encode(claims.encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), ("v1." + encoded).encode(), hashlib.sha256).digest()).decode().rstrip("=")
    with pytest.raises(AdmissionError, match="invalid_admission"):
        verify_proof("v1." + encoded + "." + signature, SECRET, "POST", "/api/ask", b"")


def test_atomic_public_cap_and_idempotent_settlement():
    async def run():
        store = MemoryQuotaStore()
        results = await asyncio.gather(*(store.reserve(Principal("synthetic", "public", str(uuid4()))) for _ in range(20)), return_exceptions=True)
        accepted = [result[0] for result in results if not isinstance(result, Exception)]
        assert len(accepted) == 3
        assert all(result.code == "quota_exhausted" for result in results if isinstance(result, Exception))
        await store.finish(accepted[0], True)
        await store.finish(accepted[0], True)
        await store.finish(accepted[0], False)  # A late disconnect cannot refund an already completed answer.
        await store.finish(accepted[1], False)
        await store.finish(accepted[1], False)
        state = await store.status(accepted[0].principal)
        assert (state["used"], state["reserved"], state["remaining"]) == (1, 1, 1)
        with pytest.raises(AdmissionError, match="attempt_replayed"):
            await store.reserve(accepted[1].principal)
    asyncio.run(run())


def test_crash_lease_releases_capacity_and_rejects_late_commit():
    clock = [time.time()]
    async def run():
        store = MemoryQuotaStore(clock=lambda: clock[0])
        old = [(await store.reserve(Principal("synthetic", "public", str(uuid4()))))[0] for _ in range(3)]
        clock[0] += LEASE_SECONDS + 1
        assert (await store.status(old[0].principal))["remaining"] == 3
        with pytest.raises(AdmissionError, match="quota_unavailable"):
            await store.finish(old[0], True)
        replacement, _ = await store.reserve(Principal("synthetic", "public", str(uuid4())))
        assert (await store.finish(replacement, True))["used"] == 1
    asyncio.run(run())


def test_request_day_is_utc8_and_settlement_stays_on_original_day():
    clock = [1788278400 - 1]  # Immediately before 2026-09-02 00:00 UTC+8.
    assert day_window(clock[0])[0] == "2026-09-01"
    async def run():
        store = MemoryQuotaStore(clock=lambda: clock[0])
        p = Principal("synthetic", "public", str(uuid4()))
        reservation, _ = await store.reserve(p)
        clock[0] += 2
        finished = await store.finish(reservation, True)
        assert finished["day"] == "2026-09-01" and finished["used"] == 1
        assert (await store.status(p))["day"] == "2026-09-02"
        assert (await store.status(p))["used"] == 0
    asyncio.run(run())


def test_founding_is_unlimited_but_attempts_are_not_replayed():
    async def run():
        store = MemoryQuotaStore()
        for _ in range(12):
            reservation, quota = await store.reserve(Principal("founder:synthetic", "founding", str(uuid4())))
            assert quota["unlimited"] and quota["remaining"] is None and quota["limit"] is None
            await store.finish(reservation, True)
        assert (await store.status(reservation.principal))["used"] == 12
        with pytest.raises(AdmissionError, match="attempt_replayed"):
            await store.reserve(reservation.principal)
    asyncio.run(run())


@pytest.mark.parametrize("flag", ["true", "yes", "typo"])
def test_enabled_without_durable_configuration_fails_closed(context_pack, monkeypatch, flag):
    monkeypatch.setenv("ASK_QUOTA_ENABLED", flag)
    monkeypatch.setenv("ASK_ADMISSION_SECRET", SECRET)
    monkeypatch.delenv("ASK_QUOTA_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("ASK_QUOTA_REDIS_REST_TOKEN", raising=False)
    with TestClient(create_app(context_pack)) as client:
        assert post(client).status_code == 503
        assert get_quota(client).json()["code"] == "quota_unavailable"


def test_explicit_disabled_mode_retains_public_contract(context_pack, monkeypatch):
    monkeypatch.delenv("ASK_QUOTA_ENABLED", raising=False)
    monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)
    with TestClient(create_app(context_pack)) as client:
        assert client.get("/api/quota").json() == {"enabled": False}
        assert client.post("/api/ask", json={"question": "职业选择"}).status_code == 200


def test_memory_cannot_be_injected_into_default_production_entry():
    with pytest.raises(ValueError):
        create_app(quota_store=MemoryQuotaStore())


def test_direct_forged_and_changed_body_never_call_provider(context_pack, enabled, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-token")
    def provider(request):
        pytest.fail("An unadmitted request cannot reach any model endpoint")
    body = b'{"question":"synthetic"}'
    with TestClient(create_app(context_pack, httpx.MockTransport(provider), quota_store=MemoryQuotaStore())) as client:
        assert client.post("/api/ask", content=body).json()["code"] == "admission_required"
        assert client.post("/api/ask", content=body + b" ", headers={HEADER: proof(body)}).json()["code"] == "invalid_admission"
        assert client.get("/api/quota").status_code == 403
        assert client.request("GET", "/api/quota", content=b"nonempty", headers={HEADER: proof(method="GET", path="/api/quota")}).json()["code"] == "invalid_admission"


def test_injected_store_does_not_bypass_missing_signing_key(context_pack, enabled, monkeypatch):
    monkeypatch.setenv("ASK_ADMISSION_SECRET", "short")
    with TestClient(create_app(context_pack, quota_store=MemoryQuotaStore())) as client:
        assert post(client).json()["code"] == "quota_unavailable"


def test_completed_answers_and_repair_deduct_once_with_typed_exhaustion(context_pack, enabled, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-token")
    calls = []
    def provider(request):
        payload = json.loads(request.content)
        assert "anon:synthetic" not in request.content.decode()
        calls.append(True)
        result = answer_for("S99" if len(calls) == 1 else "S1")
        return httpx.Response(200, json={"choices": [{"message": {"content": result.model_dump_json()}, "finish_reason": "stop"}]})
    store = MemoryQuotaStore()
    with TestClient(create_app(context_pack, httpx.MockTransport(provider), quota_store=store)) as client:
        attempt = str(uuid4())
        first = post(client, attempt=attempt)
        parsed = events(first)
        assert parsed[0][0] == "quota" and parsed[0][1]["reserved"] == 1
        assert parsed[-1][1]["quota"]["used"] == 1 and len(calls) == 2
        assert post(client, attempt=attempt).json()["code"] == "attempt_replayed"
        assert len(calls) == 2
        assert events(post(client))[-1][1]["quota"]["used"] == 2
        assert events(post(client))[-1][1]["quota"]["used"] == 3
        exhausted = post(client)
        assert exhausted.status_code == 429
        assert exhausted.headers["x-ask-error-code"] == "quota_exhausted"
        assert exhausted.json()["code"] == "quota_exhausted" and exhausted.json()["remaining"] == 0
        assert exhausted.json()["reset_at"].endswith("Z")
        assert get_quota(client).json()["used"] == 3
        assert client.app.state.slots._value == 3


def test_burst_limit_has_no_quota_exhaustion_header(context_pack, enabled, monkeypatch):
    monkeypatch.setenv("ASK_RATE_PER_MINUTE", "1")
    monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)
    with TestClient(create_app(context_pack, quota_store=MemoryQuotaStore())) as client:
        assert post(client).status_code == 200
        limited = post(client)
        assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
        assert "x-ask-error-code" not in limited.headers


@pytest.mark.parametrize("kind", ["unsupported", "clarify", "provider-failure", "search-only"])
def test_nonanswers_release_reservation(context_pack, enabled, monkeypatch, kind):
    if kind == "search-only":
        monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-token")
    def provider(request):
        if kind == "provider-failure":
            raise httpx.ReadTimeout("synthetic")
        result = answer_for().model_copy(update={"status": kind, "sections": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": result.model_dump_json()}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider), quota_store=MemoryQuotaStore())) as client:
        result = events(post(client))[-1][1]
        assert result["status"] != "answered"
        assert result["quota"]["used"] == 0 and result["quota"]["reserved"] == 0
        assert get_quota(client).json()["remaining"] == 3


def test_founding_proof_keeps_daily_quota_unlimited_but_global_slots_bounded(context_pack, enabled, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-token")
    def provider(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": answer_for().model_dump_json()}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider), quota_store=MemoryQuotaStore())) as client:
        for _ in range(5):
            result = events(post(client, subject="founder:synthetic", tier="founding"))[-1][1]
            assert result["status"] == "answered" and result["quota"]["remaining"] is None
        assert get_quota(client, subject="founder:synthetic", tier="founding").json()["used"] == 5
        assert client.app.state.slots._value == 3


def test_anyio_disconnect_refunds_and_returns_slot(context_pack, enabled, monkeypatch):
    import importlib
    app_module = importlib.import_module("server.app")
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-token")
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def generation(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        monkeypatch.setattr(app_module, "generate_answer", generation)
        store = MemoryQuotaStore()
        app = create_app(context_pack, quota_store=store)
        async with app.router.lifespan_context(app):
            scope = {"type": "http", "method": "POST", "path": "/api/ask", "headers": [],
                     "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "scheme": "http", "http_version": "1.1"}
            request = Request(scope)
            p = Principal("anon:synthetic", "public", str(uuid4()))
            request.state.principal = p
            endpoint = next(route.endpoint for route in app.routes if route.path == "/api/ask")
            response = await endpoint(request, AskRequest(question="职业选择"))
            async def consume():
                async for _ in response.body_iterator:
                    pass
            # is_disconnected must have a receive channel; this test owns cancellation.
            async def receive():
                await asyncio.sleep(0)
                return {"type": "http.request", "body": b"", "more_body": False}
            request._receive = receive
            async with anyio.create_task_group() as group:
                group.start_soon(consume)
                await asyncio.wait_for(started.wait(), 1)
                group.cancel_scope.cancel()
            assert cancelled.is_set() and app.state.slots._value == 3
            assert (await store.status(p))["remaining"] == 3
    asyncio.run(run())


def test_asgi_header_disconnect_refunds_before_iterator_starts_and_cleans_once(context_pack, enabled, monkeypatch):
    from starlette.requests import ClientDisconnect
    monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)

    async def run():
        settlements = []
        class TrackingStore(MemoryQuotaStore):
            async def finish(self, reservation, completed):
                settlements.append(completed)
                return await super().finish(reservation, completed)

        store = TrackingStore()
        def no_provider(request):
            pytest.fail("Header disconnect and search-only recovery must not call a provider")
        app = create_app(context_pack, httpx.MockTransport(no_provider), quota_store=store)
        body = b'{"question":"synthetic"}'
        principal = Principal("anon:synthetic", "public", str(uuid4()))
        scope = {"type": "http", "method": "POST", "path": "/api/ask", "raw_path": b"/api/ask",
                 "query_string": b"", "headers": [(b"content-type", b"application/json"),
                 (HEADER.lower().encode(), proof(body, attempt=principal.attempt).encode())],
                 "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000),
                 "scheme": "http", "http_version": "1.1", "asgi": {"version": "3.0", "spec_version": "2.4"}}
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()
        async def send(message):
            if message["type"] == "http.response.start":
                raise OSError("synthetic disconnect before response headers")

        async with app.router.lifespan_context(app):
            with pytest.raises((OSError, ClientDisconnect)):
                await app(scope, receive, send)
            assert app.state.slots._value == 3
            quota = await store.status(principal)
            assert (quota["used"], quota["reserved"], quota["remaining"]) == (0, 0, 3)
            assert settlements == [False]
            # Normal completion reaches iterator and response cleanup. It must
            # still settle once and release exactly one concurrency slot.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                response = await client.post("/api/ask", content=body, headers={HEADER: proof(body), "Content-Type": "application/json"})
            assert response.status_code == 200
            assert events(response)[-1][1]["quota"]["used"] == 0
            assert settlements == [False, False]
            assert app.state.slots._value == 3
    asyncio.run(run())


def test_redis_transport_is_bounded_metadata_only_and_sanitizes_failures():
    seen = []
    def provider(request):
        command = json.loads(request.content)
        seen.append(command)
        assert command[0] == "EVAL" and command[2] == 3
        assert all("synthetic" not in key for key in command[3:6])
        return httpx.Response(503, text="PRIVATE_SYNTHETIC_PROVIDER_ERROR")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            store = RedisQuotaStore("https://synthetic.upstash.io", "synthetic-not-live", client)
            with pytest.raises(AdmissionError) as error:
                await store.reserve(Principal("anon:synthetic", "public", str(uuid4())))
            assert error.value.code == "quota_unavailable"
            assert "PRIVATE_SYNTHETIC" not in json.dumps(error.value.payload())
    asyncio.run(run())
    assert len(seen) == 2 and seen[0] == seen[1]  # Same grant makes a lost-response retry idempotent.


@pytest.mark.parametrize("url", ["http://synthetic.upstash.io", "https://elsewhere.example.org", "https://user:pass@synthetic.upstash.io", "https://synthetic.upstash.io/?token=bad", "https://["])
def test_redis_destination_and_credential_configuration_are_fixed(url):
    with pytest.raises(AdmissionError):
        RedisQuotaStore(url, "synthetic", None)
