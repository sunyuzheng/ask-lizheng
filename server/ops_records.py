"""Durable v2 question admission; only explicitly disclosed fields leave APP."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from uuid import uuid4

import httpx

from .admission import AdmissionError, Principal, QUOTA_STORE_PURPOSE, derived_secret
from .query_records import QUERY_STORE_HEADER, QUERY_STORE_URL, STATUSES, WRITE_SECONDS


class OpsRecorder:
    def __init__(self, client: httpx.AsyncClient, clock=time.time):
        self.client, self.clock = client, clock
        self.enabled = os.getenv("ASK_OPS_ENABLED", "false") == "true"
        token = os.getenv("AI_BUILDER_TOKEN", "")
        self.secret = derived_secret(token, QUOTA_STORE_PURPOSE) if self.enabled and token else ""

    def prepare(self, *, question: str, created_at: str, model: str, principal: Principal,
                conversation_id: str, intent: str) -> dict:
        if not self.secret or not principal or not principal.visitor or principal.entrypoint not in {"home", "standalone"}:
            raise AdmissionError("invalid_admission")
        visitor = hmac.new(self.secret.encode("utf-8"), ("ask-ops:visitor:v2:" + principal.visitor).encode("utf-8"), hashlib.sha256).hexdigest()
        conversation = hmac.new(self.secret.encode("utf-8"), ("ask-ops:conversation:v2:" + visitor + ":" + conversation_id).encode("utf-8"), hashlib.sha256).hexdigest()
        return {"v": 2, "event": "start", "record_id": str(uuid4()), "question": question,
                "created_at": created_at, "model": model, "visitor_id": visitor,
                "conversation_id": conversation, "intent": intent, "entrypoint": principal.entrypoint}

    async def _write(self, record: dict) -> bool:
        # Reuse exactly the same UUID/body across lost-response retries. No
        # background queue: start must be acknowledged before any model call.
        body = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if not self.secret or len(body) > 16384:
            return False
        for attempt in range(2):
            expiry = int(self.clock()) + 45
            message = f"ask-ops-store:v2:{expiry}:{hashlib.sha256(body).hexdigest()}"
            signature = hmac.new(self.secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
            try:
                async with asyncio.timeout(WRITE_SECONDS):
                    async with self.client.stream("POST", QUERY_STORE_URL, content=body,
                        headers={"Content-Type": "application/octet-stream", QUERY_STORE_HEADER: f"v2.{expiry}.{signature}"},
                        follow_redirects=False, timeout=httpx.Timeout(WRITE_SECONDS)) as response:
                        if response.status_code == 200:
                            raw = bytearray()
                            async for chunk in response.aiter_bytes():
                                raw.extend(chunk)
                                if len(raw) > 4096:
                                    return False
                            ack = json.loads(raw)
                            return isinstance(ack, dict) and set(ack) == {"ok"} and ack["ok"] is True
                        if response.status_code < 500:
                            return False
            except Exception:
                if attempt:
                    return False
        return False

    async def start(self, record: dict) -> None:
        if not await self._write(record):
            raise AdmissionError("ops_storage_unavailable", 503)

    async def finish(self, record_id: str, status: str, duration_ms: int) -> bool:
        if status not in STATUSES:
            return False
        return await self._write({"v": 2, "event": "finish", "record_id": record_id,
                                 "status": status, "duration_ms": min(600_000, max(0, duration_ms))})
