"""Durable v3 question/validated-answer archive; disclosed fields only."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from uuid import uuid4

import httpx

from .admission import AdmissionError, Principal, QUOTA_STORE_PURPOSE, derived_secret
from .query_records import QUERY_STORE_HEADER, QUERY_STORE_URL, STATUSES, WRITE_SECONDS

OPS_BODY_LIMIT = 262144
# Every answered v4 question has its own page, ask.lizheng.ai/s/<date>/<word> (2026-10-14, the owner:
# the page exists once the question is answered). The finish record carries the word; lizheng.ai
# makes the page as it archives the answer, unless the question was about the asker's own situation,
# and its reply names the page, which goes out with the answer. The asker's page also gets a proof
# (same key) to make the page of an answer about their situation when they choose to, and to report
# a share: lizheng.ai checks it, so only that page can, and only under the word the answer was given.
SHARE_PREFIX = "ask-share:v1:"
SHARE_FALLBACK_WORD = "question"
PAGE_ORIGIN = "https://ask.lizheng.ai/s/"
# <day>/<slug>, as lizheng.ai names a page in its reply.
PAGE_PATH = re.compile(r"\d{4}-\d{2}-\d{2}/[a-z0-9]{1,40}(?:-[a-z0-9]{1,40}){0,4}")
ANSWER_FIELDS = {"status", "summary", "sections", "sources", "followups", "clarifying_questions", "limitations", "retryable", "failure_code"}
# lizheng.ai and ask-lizheng-ops each check archived answers against their own copy of these fields
# (shared/ask-archive-answer.ts) and reject a record with any other one. Add a field there first.
SOURCE_FIELDS = {"id", "title", "url", "date", "excerpt", "author", "source_type", "reason", "attribution_note", "evidence_role", "public_copy_url", "timecode",
                 "source_visibility", "text_access", "membership_platform", "membership_url", "membership_verified_at",
                 "transcript_source_kind", "transcript_quality", "speaker_classification",
                 "data_layer", "speaker_id", "speaker_name", "speaker_status", "fragment_id", "source_snapshot_sha256", "participant_names", "participant_entity_ids", "participant_aliases", "participant_metadata_basis", "speech_act", "content_origin", "generation_method"}


def archived_answer(result: dict) -> dict:
    """Capture the public final result, never provider JSON or request fields."""
    snapshot = {key: value for key, value in result.items() if key in ANSWER_FIELDS}
    snapshot["sources"] = [{key: value for key, value in source.items() if key in SOURCE_FIELDS}
                           for source in result["sources"]]
    # Freeze the snapshot so later quota attachment or UI state cannot alter it.
    return json.loads(json.dumps(snapshot, ensure_ascii=False))


def share_word(slug: str) -> str:
    """The model's topic words as an address word: up to four lowercase English words, hyphenated."""
    words = re.findall(r"[a-z0-9]+", str(slug).lower())[:4]
    while words and len("-".join(words)) > 40:
        words.pop()
    return "-".join(words) or SHARE_FALLBACK_WORD


class OpsRecorder:
    def __init__(self, client: httpx.AsyncClient, clock=time.time):
        self.client, self.clock = client, clock
        self.enabled = os.getenv("ASK_OPS_ENABLED", "false") == "true"
        token = os.getenv("AI_BUILDER_TOKEN", "")
        self.secret = derived_secret(token, QUOTA_STORE_PURPOSE) if self.enabled and token else ""

    def prepare(self, *, question: str, created_at: str, model: str, principal: Principal,
                conversation_id: str, intent: str, notice: str = "v3", context: str = "",
                has_history: bool = False) -> dict:
        if not self.secret or not principal or not principal.visitor or principal.entrypoint not in {"home", "standalone"}:
            raise AdmissionError("invalid_admission")
        visitor = hmac.new(self.secret.encode("utf-8"), ("ask-ops:visitor:v3:" + principal.visitor).encode("utf-8"), hashlib.sha256).hexdigest()
        conversation = hmac.new(self.secret.encode("utf-8"), ("ask-ops:conversation:v3:" + visitor + ":" + conversation_id).encode("utf-8"), hashlib.sha256).hexdigest()
        record = {"v": 3, "event": "start", "record_id": str(uuid4()), "question": question,
                  "created_at": created_at, "model": model, "visitor_id": visitor,
                  "conversation_id": conversation, "intent": intent, "entrypoint": principal.entrypoint}
        if notice == "v4":
            # Asked under the v4 notice. A situation or earlier turn may have shaped the answer,
            # so such records are marked and the automatic feed leaves them out. Since 2026-10-04
            # the situation itself is not kept (the owner's decision; the page says so where it
            # is written): it goes to the model for this answer only.
            record.update({"notice_version": "v4", "has_background": "1" if context.strip() or has_history else "0",
                           "context": ""})
        return record

    async def _write(self, record: dict) -> dict | None:
        """The acknowledgement, or None: {"ok": true}, with "share" when a page was made or found."""
        # Reuse exactly the same UUID/body across lost-response retries. No
        # background queue: start must be acknowledged before any model call.
        body = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if not self.secret or len(body) > OPS_BODY_LIMIT:
            return None
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
                                    return None
                            ack = json.loads(raw)
                            if (isinstance(ack, dict) and ack.get("ok") is True and set(ack) <= {"ok", "share"}
                                    and ("share" not in ack or (isinstance(ack["share"], str) and PAGE_PATH.fullmatch(ack["share"])))):
                                return ack
                            return None
                        if response.status_code < 500:
                            return None
            except Exception:
                if attempt:
                    return None
        return None

    async def start(self, record: dict) -> None:
        if not await self._write(record):
            raise AdmissionError("ops_storage_unavailable", 503)

    def share(self, record_id: str, slug: str = "", page: str | None = None) -> dict:
        """What the asker's page gets with the answer: the record, its address word and a proof, and
        the answer's own page once it has one."""
        word = share_word(slug)
        proof = hmac.new(self.secret.encode("utf-8"), f"{SHARE_PREFIX}{record_id}:{word}".encode("utf-8"), hashlib.sha256).hexdigest()
        return {"record_id": record_id, "word": word, "proof": proof, **({"url": PAGE_ORIGIN + page} if page else {})}

    async def finish(self, record_id: str, status: str, duration_ms: int, *, answer: dict | None = None,
                     error_code: str | None = None, page_word: str | None = None) -> dict | None:
        """The acknowledgement, or None. With page_word (an answered v4 question), lizheng.ai makes
        the answer's page as it archives it, and "share" names it."""
        if status not in STATUSES or (answer is None and status not in {"error", "cancelled"}):
            return None
        record = {"v": 3, "event": "finish", "record_id": record_id, "status": status,
                  "duration_ms": min(600_000, max(0, duration_ms)), "answer": answer, "error_code": error_code}
        if page_word and status == "answered":
            record["share_word"] = page_word
        return await self._write(record)
