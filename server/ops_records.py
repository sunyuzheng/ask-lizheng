"""Durable v3 question/validated-answer archive; disclosed fields only."""
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

OPS_BODY_LIMIT = 262144
ANSWER_FIELDS = {"status", "summary", "sections", "sources", "followups", "clarifying_questions", "limitations", "retryable", "failure_code"}
SOURCE_FIELDS = {"id", "title", "url", "date", "excerpt", "author", "source_type", "reason", "attribution_note", "evidence_role", "public_copy_url", "timecode"}


def archived_answer(result: dict) -> dict:
    """Capture the public final result, never provider JSON or request fields."""
    snapshot = {key: value for key, value in result.items() if key in ANSWER_FIELDS}
    snapshot["sources"] = [{key: value for key, value in source.items() if key in SOURCE_FIELDS}
                           for source in result["sources"]]
    # Freeze the snapshot so later quota attachment or UI state cannot alter it.
    return json.loads(json.dumps(snapshot, ensure_ascii=False))


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
        visitor = hmac.new(self.secret.encode("utf-8"), ("ask-ops:visitor:v3:" + principal.visitor).encode("utf-8"), hashlib.sha256).hexdigest()
        conversation = hmac.new(self.secret.encode("utf-8"), ("ask-ops:conversation:v3:" + visitor + ":" + conversation_id).encode("utf-8"), hashlib.sha256).hexdigest()
        return {"v": 3, "event": "start", "record_id": str(uuid4()), "question": question,
                "created_at": created_at, "model": model, "visitor_id": visitor,
                "conversation_id": conversation, "intent": intent, "entrypoint": principal.entrypoint}

    async def _write(self, record: dict) -> bool:
        # Reuse exactly the same UUID/body across lost-response retries. No
        # background queue: start must be acknowledged before any model call.
        body = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if not self.secret or len(body) > OPS_BODY_LIMIT:
            return False
        for attempt in range(2):
            expiry = int(self.clock()) + 45
            message = f"ask-ops-store:v3:{expiry}:{hashlib.sha256(body).hexdigest()}"
            signature = hmac.new(self.secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
            try:
                async with asyncio.timeout(WRITE_SECONDS):
                    async with self.client.stream("POST", QUERY_STORE_URL, content=body,
                        headers={"Content-Type": "application/octet-stream", QUERY_STORE_HEADER: f"v3.{expiry}.{signature}"},
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

    async def finish(self, record_id: str, status: str, duration_ms: int, *, answer: dict | None = None,
                     error_code: str | None = None) -> bool:
        if status not in STATUSES or (answer is None and status not in {"error", "cancelled"}):
            return False
        return await self._write({"v": 3, "event": "finish", "record_id": record_id,
                                 "status": status, "duration_ms": min(600_000, max(0, duration_ms)),
                                 "answer": answer, "error_code": error_code})
