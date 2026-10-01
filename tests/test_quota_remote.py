"""Fixed public transport plus durable Lua, using synthetic keys and no network."""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

import httpx
import pytest

from server.admission import ADMISSION_PURPOSE, QUOTA_STORE_PURPOSE, AdmissionError, Principal, derived_secret
from server.quota import SCRIPT, QUOTA_STORE_HEADER, QUOTA_STORE_URL, MemoryQuotaStore, QuotaAdmission, RemoteQuotaStore

TOKEN = "synthetic-provider-key-not-a-live-credential"


def verified_command(request, now):
    assert str(request.url) == QUOTA_STORE_URL
    assert request.headers["content-type"] == "application/octet-stream"
    assert "authorization" not in request.headers and "cookie" not in request.headers
    body = request.content
    command = json.loads(body)
    assert body == json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(body) <= 16384 and command[:3] == ["EVAL", SCRIPT, 3]
    version, raw_expiry, signature = request.headers[QUOTA_STORE_HEADER].split(".")
    assert version == "v1" and int(raw_expiry) == int(now) + 45
    key = hmac.new(TOKEN.encode(), QUOTA_STORE_PURPOSE.encode(), hashlib.sha256).hexdigest().encode()
    message = f"ask-quota-store:v1:{raw_expiry}:{hashlib.sha256(body).hexdigest()}".encode()
    assert hmac.compare_digest(signature, hmac.new(key, message, hashlib.sha256).hexdigest())
    # This cannot be verified with an admission-purpose key or modified body.
    other_key = derived_secret(TOKEN, ADMISSION_PURPOSE).encode()
    assert not hmac.compare_digest(signature, hmac.new(other_key, message, hashlib.sha256).hexdigest())
    changed_message = f"ask-quota-store:v1:{raw_expiry}:{hashlib.sha256(body + b' ').hexdigest()}".encode()
    assert not hmac.compare_digest(signature, hmac.new(key, changed_message, hashlib.sha256).hexdigest())
    return command


def test_derived_keys_match_native_hmac_and_are_purpose_separated():
    for purpose in [ADMISSION_PURPOSE, QUOTA_STORE_PURPOSE]:
        key = derived_secret(TOKEN, purpose)
        assert key == hmac.new(TOKEN.encode(), purpose.encode(), hashlib.sha256).hexdigest()
        assert len(key) == 64
    assert derived_secret(TOKEN, ADMISSION_PURPOSE) != derived_secret(TOKEN, QUOTA_STORE_PURPOSE)
    for token, purpose in [("", ADMISSION_PURPOSE), (TOKEN, "other-purpose")]:
        with pytest.raises(AdmissionError, match="quota_unavailable"):
            derived_secret(token, purpose)


def test_remote_uses_signed_compact_bytes_and_fixed_metadata_only_endpoint():
    now = time.time()
    async def run():
        async def transport(request):
            command = verified_command(request, now)
            assert command[6] == "status" and command[10] == ""
            assert all("synthetic-subject" not in key for key in command[3:6])
            return httpx.Response(200, json={"result": ["OK", 1, 0]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            store = RemoteQuotaStore(QUOTA_STORE_URL, TOKEN, client, lambda: now)
            result = await store.status(Principal("synthetic-subject", "public", str(uuid4())))
            assert result["used"] == 1 and result["remaining"] == 2
    asyncio.run(run())


@pytest.mark.parametrize("url", ["http://www.lizheng.ai/api/ask-lizheng/quota-storage", "https://ask.lizheng.ai/api/ask-lizheng/quota-storage",
    QUOTA_STORE_URL + "/", QUOTA_STORE_URL + "?token=x", "https://www.lizheng.ai/other-path", "https://elsewhere.example/api/ask-lizheng/quota-storage"])
def test_remote_rejects_unapproved_destinations_before_requests(url):
    with pytest.raises(AdmissionError, match="quota_unavailable"):
        RemoteQuotaStore(url, TOKEN, None)


@pytest.mark.parametrize("failure", ["redirect", "invalid-result"])
def test_remote_does_not_follow_redirects_or_echo_untrusted_error_bodies(failure):
    calls = []
    async def run():
        def transport(request):
            calls.append(str(request.url))
            if failure == "redirect":
                return httpx.Response(307, headers={"Location": "https://elsewhere.example/collect"}, text="SYNTHETIC_PRIVATE_SENTINEL")
            return httpx.Response(200, json={"result": ["OK", "SYNTHETIC_PRIVATE_SENTINEL", 0]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
            store = RemoteQuotaStore(QUOTA_STORE_URL, TOKEN, client)
            with pytest.raises(AdmissionError) as error:
                await store.reserve(Principal("synthetic", "public", str(uuid4())))
            assert error.value.code == "quota_unavailable" and "SYNTHETIC_PRIVATE_SENTINEL" not in json.dumps(error.value.payload())
    asyncio.run(run())
    assert calls == [QUOTA_STORE_URL, QUOTA_STORE_URL]


def test_remote_bounds_total_request_time_and_closes_idle_response(monkeypatch):
    monkeypatch.setattr("server.quota.STORE_REQUEST_SECONDS", .025)
    closed = []
    class Idle(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"result":'
            await asyncio.Event().wait()
        async def aclose(self): closed.append(True)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Idle()))) as client:
            with pytest.raises(AdmissionError, match="quota_unavailable"):
                await RemoteQuotaStore(QUOTA_STORE_URL, TOKEN, client).reserve(Principal("synthetic", "public", str(uuid4())))
    asyncio.run(run())
    assert len(closed) == 2


def test_admission_derives_injected_key_but_explicit_key_takes_precedence(monkeypatch):
    monkeypatch.setenv("ASK_QUOTA_ENABLED", "true")
    monkeypatch.setenv("AI_BUILDER_TOKEN", TOKEN)
    monkeypatch.setenv("ASK_QUOTA_STORE_ORIGIN", QUOTA_STORE_URL)
    for key in ["ASK_ADMISSION_SECRET", "ASK_QUOTA_REDIS_REST_URL", "ASK_QUOTA_REDIS_REST_TOKEN"]:
        monkeypatch.delenv(key, raising=False)
    admission = QuotaAdmission(None)
    assert admission.enabled and admission.ready and isinstance(admission.store, RemoteQuotaStore)
    assert admission.secret == derived_secret(TOKEN, ADMISSION_PURPOSE)
    monkeypatch.setenv("ASK_ADMISSION_SECRET", "explicit-test-only-secret-at-least-32-bytes")
    assert QuotaAdmission(None).secret == "explicit-test-only-secret-at-least-32-bytes"
    monkeypatch.setenv("ASK_ADMISSION_SECRET", "short")
    assert not QuotaAdmission(None).ready  # Never rescue an explicitly invalid configured key.


def test_derived_admission_verifies_relay_proof_and_preserves_route_body_binding(monkeypatch):
    now, body, attempt = time.time(), b'{"question":"synthetic"}', str(uuid4())
    monkeypatch.setenv("ASK_QUOTA_ENABLED", "true")
    monkeypatch.setenv("AI_BUILDER_TOKEN", TOKEN)
    monkeypatch.setenv("ASK_QUOTA_STORE_ORIGIN", QUOTA_STORE_URL)
    for key in ["ASK_ADMISSION_SECRET", "ASK_QUOTA_REDIS_REST_URL", "ASK_QUOTA_REDIS_REST_TOKEN"]:
        monkeypatch.delenv(key, raising=False)
    admission = QuotaAdmission(None, clock=lambda: now)
    claims = {"v": 1, "sub": "synthetic", "tier": "public", "attempt": attempt, "exp": int(now) + 45,
        "method": "POST", "path": "/api/ask", "body_sha256": hashlib.sha256(body).hexdigest()}
    b64 = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    encoded = b64(json.dumps(claims, separators=(",", ":")).encode())
    # Use the wire contract independently, as the relay does; hex text is the key.
    key = hmac.new(TOKEN.encode(), ADMISSION_PURPOSE.encode(), hashlib.sha256).hexdigest().encode()
    signature = b64(hmac.new(key, ("v1." + encoded).encode(), hashlib.sha256).digest())
    proof = f"v1.{encoded}.{signature}"
    assert admission.verify(proof, "POST", "/api/ask", body) == Principal("synthetic", "public", attempt)
    for method, path, changed_body in [("GET", "/api/quota", body), ("POST", "/api/ask", body + b" ")]:
        with pytest.raises(AdmissionError, match="invalid_admission"):
            admission.verify(proof, method, path, changed_body)


def test_local_explicit_redis_remains_usable_without_provider_key(monkeypatch):
    monkeypatch.setenv("ASK_QUOTA_ENABLED", "true")
    monkeypatch.setenv("ASK_ADMISSION_SECRET", "explicit-local-secret-at-least-32-bytes")
    monkeypatch.setenv("ASK_QUOTA_REDIS_REST_URL", "https://synthetic.upstash.io")
    monkeypatch.setenv("ASK_QUOTA_REDIS_REST_TOKEN", "synthetic-local-redis")
    monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)
    monkeypatch.delenv("ASK_QUOTA_STORE_ORIGIN", raising=False)
    admission = QuotaAdmission(None)
    assert admission.ready and not isinstance(admission.store, RemoteQuotaStore)


@pytest.mark.parametrize("flag,token,origin", [("true", "", QUOTA_STORE_URL), ("true", TOKEN, ""),
    ("true", TOKEN, "https://elsewhere.example"), ("typo", TOKEN, QUOTA_STORE_URL)])
def test_remote_activation_missing_or_invalid_configuration_fails_closed(monkeypatch, flag, token, origin):
    monkeypatch.setenv("ASK_QUOTA_ENABLED", flag)
    monkeypatch.setenv("AI_BUILDER_TOKEN", token)
    monkeypatch.setenv("ASK_QUOTA_STORE_ORIGIN", origin)
    for key in ["ASK_ADMISSION_SECRET", "ASK_QUOTA_REDIS_REST_URL", "ASK_QUOTA_REDIS_REST_TOKEN"]:
        monkeypatch.delenv(key, raising=False)
    admission = QuotaAdmission(None)
    assert admission.enabled and not admission.ready
    with pytest.raises(AdmissionError, match="quota_unavailable"):
        admission.verify(None, "POST", "/api/ask", b"")


@pytest.mark.parametrize("lost_action", ["reserve", "commit", "release"])
def test_real_lua_remote_lost_response_preserves_grant_and_settles_once(lost_action):
    fakeredis = pytest.importorskip("fakeredis")
    pytest.importorskip("lupa")
    now = time.time()
    async def run():
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        requests, dropped = [], False
        async def transport(request):
            nonlocal dropped
            command = verified_command(request, now)
            requests.append((command[6], request.content))
            result = await redis.execute_command(*command)
            if command[6] == lost_action and not dropped:
                dropped = True
                raise httpx.ReadError("synthetic dropped response")
            return httpx.Response(200, json={"result": result})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            store = RemoteQuotaStore(QUOTA_STORE_URL, TOKEN, client, lambda: now)
            reservation, initial = await store.reserve(Principal("synthetic", "public", str(uuid4())))
            assert initial["reserved"] == 1
            result = await store.finish(reservation, lost_action != "release")
            assert (result["used"], result["reserved"]) == (0 if lost_action == "release" else 1, 0)
            retried = [body for action, body in requests if action == lost_action]
            assert dropped and len(retried) == 2 and retried[0] == retried[1]
        await redis.aclose()
    asyncio.run(run())


def test_real_lua_remote_atomic_cap_across_independent_process_clients():
    fakeredis = pytest.importorskip("fakeredis")
    pytest.importorskip("lupa")
    now = time.time()
    async def run():
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        async def transport(request):
            command = verified_command(request, now)
            return httpx.Response(200, json={"result": await redis.execute_command(*command)})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            stores = [RemoteQuotaStore(QUOTA_STORE_URL, TOKEN, client, lambda: now) for _ in range(20)]
            results = await asyncio.gather(*(store.reserve(Principal("synthetic", "public", str(uuid4()))) for store in stores), return_exceptions=True)
            admitted = [value for value in results if not isinstance(value, Exception)]
            assert len(admitted) == 3
            assert all(error.code == "quota_exhausted" for error in results if isinstance(error, Exception))
        await redis.aclose()
    asyncio.run(run())
