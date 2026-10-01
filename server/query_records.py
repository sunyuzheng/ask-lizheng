"""Best-effort bounded writes of the explicitly authorized submitted question."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from uuid import uuid4

import httpx

from .admission import QUOTA_STORE_PURPOSE, derived_secret

QUERY_STORE_URL = "https://www.lizheng.ai/api/ask-lizheng/query-storage"
QUERY_STORE_HEADER = "X-Ask-Query-Proof"
WRITE_SECONDS = 2
MAX_PENDING = 32
STATUSES = {"answered", "clarify", "unsupported", "sources-only", "error", "cancelled"}


class QueryRecorder:
    def __init__(self, client: httpx.AsyncClient, clock=time.time):
        self.client, self.clock = client, clock
        self.tasks: set[asyncio.Task] = set()
        self.slots = asyncio.Semaphore(2)
        self.closed = False
        self.secret = ""
        if os.getenv("ASK_QUERY_LOG_ENABLED", "false") == "true":
            token = os.getenv("AI_BUILDER_TOKEN", "")
            if token:
                self.secret = derived_secret(token, QUOTA_STORE_PURPOSE)

    def enqueue(self, *, question: str, created_at: str, model: str, status: str, duration_ms: int) -> None:
        # This synchronous admission never waits on storage or model slots.
        if not self.secret or self.closed or len(self.tasks) >= MAX_PENDING:
            return
        if status not in STATUSES or not 1 <= len(question.strip()) <= 2000 or not 0 <= duration_ms <= 600_000:
            return
        record = {"v": 1, "record_id": str(uuid4()), "question": question,
                  "created_at": created_at, "model": model, "status": status, "duration_ms": duration_ms}
        task = asyncio.create_task(self._write(record))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _write(self, record: dict) -> None:
        try:
            body = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) > 16384:
                return
            async with self.slots:
                for attempt in range(2):
                    expiry = int(self.clock()) + 45
                    message = f"ask-query-store:v1:{expiry}:{hashlib.sha256(body).hexdigest()}"
                    signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
                    try:
                        async with asyncio.timeout(WRITE_SECONDS):
                            response = await self.client.post(QUERY_STORE_URL, content=body,
                                headers={"Content-Type": "application/octet-stream", QUERY_STORE_HEADER: f"v1.{expiry}.{signature}"},
                                follow_redirects=False, timeout=httpx.Timeout(WRITE_SECONDS))
                        if response.status_code == 200:
                            return
                        if response.status_code < 500:
                            return
                    except (httpx.HTTPError, TimeoutError):
                        if attempt:
                            return
        except asyncio.CancelledError:
            raise
        except Exception:
            # Neither rejected text nor an upstream exception may reach logs.
            return

    async def close(self) -> None:
        self.closed = True
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
