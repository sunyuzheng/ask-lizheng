"""Metadata-only daily reservations, atomically enforced by durable Redis EVAL."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .admission import ADMISSION_PURPOSE, QUOTA_STORE_PURPOSE, AdmissionError, Principal, derived_secret, verify_proof

DAY_ZONE = timezone(timedelta(hours=8))
LIMIT = 3
LEASE_SECONDS = 150
RETENTION_SECONDS = 172800
QUOTA_STORE_URL = "https://www.lizheng.ai/api/ask-lizheng/quota-storage"
QUOTA_STORE_HEADER = "X-Ask-Quota-Proof"
STORE_REQUEST_SECONDS = 2


def day_window(now: float) -> tuple[str, int]:
    local = datetime.fromtimestamp(now, DAY_ZONE)
    reset = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return local.date().isoformat(), int(reset.timestamp())


def snapshot(tier: str, day: str, reset: int, used: int, pending: int) -> dict:
    return {"enabled": True, "tier": tier, "unlimited": tier == "founding", "limit": None if tier == "founding" else LIMIT,
            "used": used, "reserved": pending, "remaining": None if tier == "founding" else max(0, LIMIT - used - pending),
            "day": day, "reset_at": datetime.fromtimestamp(reset, timezone.utc).isoformat().replace("+00:00", "Z")}


@dataclass(frozen=True)
class Reservation:
    principal: Principal
    day: str
    reset: int
    grant: str


# All keys use one Redis hash tag. Pending leases are removed inside the same
# atomic script as admission; no read-then-write counter races or lost refunds.
SCRIPT = r"""
local action, now, lim = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
local attempt, grant, day = ARGV[4], ARGV[5], ARGV[6]
local reset, lease, retention = tonumber(ARGV[7]), tonumber(ARGV[8]), tonumber(ARGV[9])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local pending = redis.call('ZCARD', KEYS[2])
local state = redis.call('HGET', KEYS[3], 'state')
local same = redis.call('HGET', KEYS[3], 'grant') == grant and redis.call('HGET', KEYS[3], 'day') == day
local code = 'OK'
if action == 'reserve' then
  if state then
    if not (state == 'pending' and same and tonumber(redis.call('HGET', KEYS[3], 'lease') or '0') > now) then code = 'DUPLICATE' end
  elseif lim > 0 and used + pending >= lim then code = 'EXHAUSTED'
  else
    redis.call('HSET', KEYS[3], 'state', 'pending', 'grant', grant, 'day', day, 'lease', now + lease)
    redis.call('EXPIRE', KEYS[3], retention)
    redis.call('ZADD', KEYS[2], now + lease, attempt)
  end
elseif action == 'commit' then
  if state == 'pending' and same and tonumber(redis.call('HGET', KEYS[3], 'lease') or '0') > now and redis.call('ZSCORE', KEYS[2], attempt) then
    redis.call('ZREM', KEYS[2], attempt)
    redis.call('INCR', KEYS[1])
    redis.call('HSET', KEYS[3], 'state', 'committed')
  elseif not (state == 'committed' and same) then code = 'EXPIRED' end
elseif action == 'release' then
  if state == 'pending' and same then
    redis.call('ZREM', KEYS[2], attempt)
    redis.call('HSET', KEYS[3], 'state', 'released')
  elseif not same then code = 'EXPIRED' end
elseif action ~= 'status' then return redis.error_reply('invalid operation') end
redis.call('EXPIREAT', KEYS[1], reset + retention)
redis.call('EXPIREAT', KEYS[2], reset + retention)
return {code, tonumber(redis.call('GET', KEYS[1]) or '0'), redis.call('ZCARD', KEYS[2])}
"""


class RedisQuotaStore:
    def __init__(self, url: str, token: str, client: httpx.AsyncClient, clock=time.time):
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise AdmissionError("quota_unavailable", 503) from None
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".upstash.io") or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or not token:
            raise AdmissionError("quota_unavailable", 503)
        self.url, self.token, self.client, self.clock = url.rstrip("/"), token, client, clock

    async def _request(self, command: list) -> httpx.Response:
        return await self.client.post(self.url, json=command, headers={"Authorization": "Bearer " + self.token},
                                      timeout=httpx.Timeout(STORE_REQUEST_SECONDS, connect=min(1, STORE_REQUEST_SECONDS), pool=min(1, STORE_REQUEST_SECONDS)), follow_redirects=False)

    async def _run(self, action: str, reservation: Reservation) -> dict:
        p, day, reset = reservation.principal, reservation.day, reservation.reset
        digest = hashlib.sha256(p.subject.encode("ascii")).hexdigest()
        prefix = "ask-quota:v1:{" + digest + "}:"
        keys = [prefix + day + ":used", prefix + day + ":pending", prefix + "attempt:" + p.attempt]
        command = ["EVAL", SCRIPT, 3, *keys, action, int(self.clock()), 0 if p.tier == "founding" else LIMIT,
                   p.attempt, reservation.grant, day, reset, LEASE_SECONDS, RETENTION_SECONDS]
        for attempt in range(2):
            try:
                async with asyncio.timeout(STORE_REQUEST_SECONDS):
                    response = await self._request(command)
                if not response.is_success:
                    raise ValueError("store unavailable")
                result = response.json().get("result")
                if not isinstance(result, list) or len(result) != 3 or result[0] not in {"OK", "DUPLICATE", "EXHAUSTED", "EXPIRED"} or any(type(number) is not int or number < 0 for number in result[1:]):
                    raise ValueError("store response")
                quota = snapshot(p.tier, day, reset, result[1], result[2])
                if result[0] == "EXHAUSTED":
                    raise AdmissionError("quota_exhausted", 429, quota)
                if result[0] == "DUPLICATE":
                    raise AdmissionError("attempt_replayed", 409, quota)
                if result[0] == "EXPIRED":
                    raise AdmissionError("quota_unavailable", 503)
                return quota
            except AdmissionError:
                raise
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError, AttributeError):
                if attempt:
                    raise AdmissionError("quota_unavailable", 503) from None
        raise AdmissionError("quota_unavailable", 503)

    async def reserve(self, principal: Principal) -> tuple[Reservation, dict]:
        day, reset = day_window(self.clock())
        reservation = Reservation(principal, day, reset, str(uuid4()))
        return reservation, await self._run("reserve", reservation)

    async def finish(self, reservation: Reservation, completed: bool) -> dict:
        return await self._run("commit" if completed else "release", reservation)

    async def status(self, principal: Principal) -> dict:
        day, reset = day_window(self.clock())
        return await self._run("status", Reservation(principal, day, reset, ""))


class RemoteQuotaStore(RedisQuotaStore):
    """Fixed signed metadata transport; the origin owns durable Redis EVAL."""
    def __init__(self, url: str, provider_token: str, client: httpx.AsyncClient, clock=time.time):
        if url != QUOTA_STORE_URL:
            raise AdmissionError("quota_unavailable", 503)
        self.url, self.client, self.clock = url, client, clock
        self.secret = derived_secret(provider_token, QUOTA_STORE_PURPOSE)

    async def _request(self, command: list) -> httpx.Response:
        body = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > 16384:
            raise AdmissionError("quota_unavailable", 503)
        expiry = int(self.clock()) + 45
        message = f"ask-quota-store:v1:{expiry}:{hashlib.sha256(body).hexdigest()}"
        signature = hmac.new(self.secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        return await self.client.post(self.url, content=body,
            headers={"Content-Type": "application/octet-stream", QUOTA_STORE_HEADER: f"v1.{expiry}.{signature}"},
            timeout=httpx.Timeout(STORE_REQUEST_SECONDS, connect=min(1, STORE_REQUEST_SECONDS), pool=min(1, STORE_REQUEST_SECONDS)), follow_redirects=False)


class MemoryQuotaStore:
    """Explicitly injectable local/test store; never selected by environment."""
    def __init__(self, clock=time.time):
        self.clock, self.lock = clock, asyncio.Lock()
        self.days, self.attempts = {}, {}

    def _state(self, principal, day):
        now = self.clock()
        for key, record in list(self.attempts.items()):
            if record["expires"] <= now:
                del self.attempts[key]
        state = self.days.setdefault((principal.subject, day), {"used": 0, "pending": {}})
        state["pending"] = {attempt: expiry for attempt, expiry in state["pending"].items() if expiry > now}
        return state

    async def reserve(self, principal):
        async with self.lock:
            day, reset = day_window(self.clock())
            state = self._state(principal, day)
            quota = snapshot(principal.tier, day, reset, state["used"], len(state["pending"]))
            key = (principal.subject, principal.attempt)
            if key in self.attempts:
                raise AdmissionError("attempt_replayed", 409, quota)
            if principal.tier == "public" and quota["remaining"] == 0:
                raise AdmissionError("quota_exhausted", 429, quota)
            reservation = Reservation(principal, day, reset, str(uuid4()))
            state["pending"][principal.attempt] = self.clock() + LEASE_SECONDS
            self.attempts[key] = {"state": "pending", "reservation": reservation, "expires": self.clock() + RETENTION_SECONDS}
            return reservation, snapshot(principal.tier, day, reset, state["used"], len(state["pending"]))

    async def finish(self, reservation, completed):
        async with self.lock:
            p = reservation.principal
            state = self._state(p, reservation.day)
            record = self.attempts.get((p.subject, p.attempt))
            if not record or record["reservation"] != reservation:
                raise AdmissionError("quota_unavailable", 503)
            if record["state"] == "pending":
                alive = p.attempt in state["pending"]
                state["pending"].pop(p.attempt, None)
                if completed and not alive:
                    raise AdmissionError("quota_unavailable", 503)
                record["state"] = "committed" if completed else "released"
                if completed:
                    state["used"] += 1
            elif completed and record["state"] != "committed":
                raise AdmissionError("quota_unavailable", 503)
            return snapshot(p.tier, reservation.day, reservation.reset, state["used"], len(state["pending"]))

    async def status(self, principal):
        async with self.lock:
            day, reset = day_window(self.clock())
            state = self._state(principal, day)
            return snapshot(principal.tier, day, reset, state["used"], len(state["pending"]))


class QuotaAdmission:
    def __init__(self, client, store=None, clock=time.time):
        raw = os.getenv("ASK_QUOTA_ENABLED", "false").lower()
        self.enabled = raw != "false" and raw != "0" and raw != ""
        self.secret, self.clock = os.getenv("ASK_ADMISSION_SECRET", ""), clock
        self.store, self.ready = store, not self.enabled
        if self.enabled and raw not in {"true", "1"}:
            return
        if self.enabled and not self.secret:
            token = os.getenv("AI_BUILDER_TOKEN", "")
            if token:
                self.secret = derived_secret(token, ADMISSION_PURPOSE)
        if self.enabled and len(self.secret.encode("utf-8")) >= 32:
            try:
                url, token = os.getenv("ASK_QUOTA_REDIS_REST_URL", ""), os.getenv("ASK_QUOTA_REDIS_REST_TOKEN", "")
                if store is not None:
                    self.store = store
                elif url or token:
                    self.store = RedisQuotaStore(url, token, client, clock)
                else:
                    self.store = RemoteQuotaStore(os.getenv("ASK_QUOTA_STORE_ORIGIN", ""), os.getenv("AI_BUILDER_TOKEN", ""), client, clock)
                self.ready = True
            except AdmissionError:
                self.ready = False

    def verify(self, proof, method, path, body):
        if not self.enabled:
            return None
        if not self.ready:
            raise AdmissionError("quota_unavailable", 503)
        return verify_proof(proof, self.secret, method, path, body, self.clock())

    async def reserve(self, principal):
        if not self.enabled:
            return None, None
        if principal is None or not self.ready:
            raise AdmissionError("quota_unavailable", 503)
        return await self.store.reserve(principal)

    async def finish(self, reservation, completed=False):
        return await self.store.finish(reservation, completed) if reservation is not None else None
