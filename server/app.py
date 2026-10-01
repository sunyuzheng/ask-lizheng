"""One-process FastAPI server; question payloads never enter application logs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Literal
from uuid import UUID

import httpx
import anyio
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .answers import DEFAULT_MODEL, ProviderFailure, assemble_answer, attribution_clarification, generate_answer, model_options, sources_only, unsupported_result
from .retrieval import ContextIndex
from .semantic import SemanticIndex
from .admission import AdmissionError, HEADER
from .quota import QuotaAdmission
from .query_records import QueryRecorder
from .ops_records import OpsRecorder

ROOT = Path(__file__).resolve().parents[1]
Intent = Literal["understand", "apply", "find"]
STREAM_HEARTBEAT_SECONDS = 5
# SSE comments keep connections active without inventing progress or exposing
# model reasoning. Padding also flushes small frames through buffering proxies.
STREAM_HEARTBEAT = ": keep-alive " + " " * 16384 + "\n\n"


class CleanupStreamingResponse(StreamingResponse):
    """Release admission resources even if sending headers fails first."""
    def __init__(self, content, *, on_close: Callable[[], Awaitable[None]], **kwargs):
        super().__init__(content, **kwargs)
        self.on_close = on_close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.on_close()


class HistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(max_length=2000)
    summary: str = Field(max_length=1500)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    context: str = Field(default="", max_length=2500)
    intent: Intent = "understand"
    history: list[HistoryItem] = Field(default_factory=list, max_length=6)
    query_log_notice: Literal["v1", "v2"] | None = None
    conversation_id: str | None = Field(default=None, max_length=36)

    @field_validator("conversation_id")
    @classmethod
    def canonical_conversation(cls, value):
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Invalid conversation UUID")
        return value

    @model_validator(mode="after")
    def ops_notice_requires_conversation(self):
        if self.query_log_notice == "v2" and self.conversation_id is None:
            raise ValueError("Conversation UUID required")
        return self

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Blank question")
        return value


class RateLimiter:
    """Bounded, short-lived salted counters; no request text or raw IP storage."""
    def __init__(self, limit: int = 12):
        self.limit = limit
        self.salt = secrets.token_bytes(16)
        self.buckets = {}

    def allow(self, address: str, route: str, multiplier: int = 1) -> bool:
        now = time.monotonic()
        key = hashlib.sha256(self.salt + address.encode() + route.encode()).digest()
        if len(self.buckets) >= 3000:
            self.buckets = {key: bucket for key, bucket in self.buckets.items() if bucket and bucket[-1] > now - 60}
        if key not in self.buckets and len(self.buckets) >= 3000:
            return False
        bucket = self.buckets.setdefault(key, deque())
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= self.limit * multiplier:
            return False
        bucket.append(now)
        return True


def sse(event: str, data: dict) -> str:
    return "event: " + event + "\ndata: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"


FAILURES = {
    "provider_busy": "模型暂时繁忙，已保留检索到的材料。稍后可以再试。",
    "model_unavailable": "模型服务暂时不可用，已保留检索到的材料。",
    "provider_timeout": "回答等待超时，已保留检索到的材料。稍后可以再试。",
    "provider_unavailable": "模型服务连接失败，已保留检索到的材料。稍后可以再试。",
    "invalid_answer": "这次回答未通过来源核对，已保留检索到的材料。可以换一种问法再试。",
}


def answer_approach(intent: str, passages, cards: list[dict]) -> dict:
    """Public reading outline, not the model's private reasoning or a conclusion."""
    summaries = {
        "understand": "先对照相关原文，解释其中的关键关系，再检查它们在什么条件下成立。",
        "apply": "先对照资料和你提供的处境，区分材料中的观点、适用条件与AI的应用推演。",
        "find": "先挑选最值得读的出处，说明每份材料适合核对什么，以及可以从哪里开始。",
    }
    questions = list(dict.fromkeys(
        question for card in cards for question in card.get("diagnostic_questions", [])[:1]
    ))[:2]
    return {
        "summary": summaries[intent],
        "questions": questions,
        "sources": [{"id": passage.source["id"], "title": passage.source["title"]}
                    for passage in passages if not passage.discovery][:3],
        "note": "这是结合候选材料的整理方向，尚不是完整回答或已核验的结论。",
    }


def create_app(context_root: Path | None = None, provider_transport=None, *, quota_store=None, query_record_transport=None) -> FastAPI:
    if quota_store is not None and context_root is None:
        raise ValueError("An injected quota store requires an explicit local/test context root")
    @asynccontextmanager
    async def lifespan(application):
        index = ContextIndex(context_root or ROOT / "data" / "context", require_lock=context_root is None)
        try:
            await asyncio.to_thread(index.load)
        except (FileNotFoundError, ValueError, OSError):
            # A partial load must not make an invalid release look healthy.
            index = ContextIndex(context_root or ROOT / "data" / "context", require_lock=context_root is None)
        application.state.index = index
        semantic = SemanticIndex(context_root or ROOT / "data" / "context", index.documents)
        await asyncio.to_thread(semantic.load)
        application.state.semantic = semantic
        application.state.limiter = RateLimiter(int(os.getenv("ASK_RATE_PER_MINUTE", "12")))
        application.state.slots = asyncio.Semaphore(max(1, min(6, int(os.getenv("ASK_CONCURRENCY", "3")))))
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(85, connect=10, pool=5),
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=3),
            transport=provider_transport,
        ) as client:
            application.state.provider = client
            async with httpx.AsyncClient(limits=httpx.Limits(max_connections=4, max_keepalive_connections=2)) as quota_client:
                application.state.quota = QuotaAdmission(quota_client, store=quota_store)
                async with httpx.AsyncClient(limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                                             transport=query_record_transport) as record_client:
                    application.state.query_records = QueryRecorder(record_client)
                    application.state.ops_records = OpsRecorder(record_client)
                    try:
                        yield
                    finally:
                        await application.state.query_records.close()

    application = FastAPI(title="Ask Lizheng", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @application.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # FastAPI's default detail echoes the invalid input. Keep both errors
        # and any upstream logging integration free of questions/history.
        return JSONResponse(status_code=422, content={"message": "请填写问题，并把问题控制在 2000 字、背景控制在 2500 字以内。", "code": "invalid_input"})

    @application.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail), "code": "rate_limited" if exc.status_code == 429 else "unavailable"}, headers=exc.headers)

    @application.exception_handler(AdmissionError)
    async def admission_error(request, exc):
        headers = {"Cache-Control": "no-store"}
        if exc.code == "quota_exhausted" and exc.status == 429:
            headers["X-Ask-Error-Code"] = "quota_exhausted"
        return JSONResponse(status_code=exc.status, content=exc.payload(), headers=headers)

    @application.middleware("http")
    async def request_boundaries(request, call_next):
        if request.url.path == "/api/ask":
            size = request.headers.get("content-length")
            if size and (not size.isdigit() or int(size) > 80000):
                return JSONResponse(status_code=413, content={"message": "这次输入过长，请缩短问题或对话背景。", "code": "input_too_large"})
            # Enforce the body limit also for chunked requests. Replaying the
            # cached Starlette body lets FastAPI parse the exact checked bytes.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 80000:
                    return JSONResponse(status_code=413, content={"message": "这次输入过长，请缩短问题或对话背景。", "code": "input_too_large"})
            request._body = bytes(body)
        if request.url.path in {"/api/ask", "/api/quota"}:
            try:
                if request.url.path == "/api/quota":
                    async for chunk in request.stream():
                        if chunk:
                            raise AdmissionError("invalid_admission")
                body = await request.body() if request.url.path == "/api/ask" else b""
                request.state.principal = application.state.quota.verify(request.headers.get(HEADER), request.method, request.url.path, body)
            except AdmissionError as exc:
                return JSONResponse(status_code=exc.status, content=exc.payload(), headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = (
                "no-store, no-transform"
                if response.headers.get("content-type", "").startswith("text/event-stream")
                else "no-store"
            )
        return response

    def rate_check(request: Request, route: str, multiplier: int = 1, subject: str | None = None):
        address = subject or (request.client.host if request.client else "unknown")
        if not application.state.limiter.allow(address, route, multiplier):
            raise HTTPException(429, "提问有点密集，请稍等一分钟再试。", headers={"Retry-After": "60"})

    @application.get("/health")
    async def health():
        ready = bool(application.state.index.documents)
        return JSONResponse({"status": "ok" if ready else "unavailable", "context_ready": ready}, status_code=200 if ready else 503)

    @application.get("/api/meta")
    async def metadata():
        ready = bool(os.getenv("AI_BUILDER_TOKEN"))
        model = os.getenv("AI_MODEL", DEFAULT_MODEL)
        return {**application.state.index.metadata(), "model_ready": ready, "semantic_ready": application.state.semantic.ready,
                "model": model, "reasoning_effort": model_options(model).get("reasoning_effort"), "mode": "live" if ready else "search-only",
                "query_logging": {"enabled": bool(application.state.query_records.secret and application.state.quota.enabled and application.state.quota.ready), "retention_days": 30},
                "ops_logging": {"enabled": ops_ready(), "retention": "until_deleted"}}

    def ops_ready():
        return bool(application.state.ops_records.enabled and application.state.ops_records.secret and application.state.query_records.secret
                    and application.state.quota.enabled and application.state.quota.ready)

    @application.get("/api/search")
    async def search(request: Request, q: str = Query(min_length=1, max_length=2000), intent: Intent = "find"):
        rate_check(request, "search", 3)
        if not q.strip():
            raise HTTPException(422, "请输入一个主题或问题。")
        passages = await asyncio.to_thread(application.state.index.retrieve, q, limit=8)
        return {"sources": [passage.source for passage in passages]}

    @application.get("/api/quota")
    async def quota_status(request: Request):
        if not application.state.quota.enabled:
            return {"enabled": False}
        principal = request.state.principal
        rate_check(request, "quota", 3, principal.subject)
        return await application.state.quota.store.status(principal)

    @application.post("/api/ask")
    async def ask(request: Request, payload: AskRequest):
        principal = request.state.principal if application.state.quota.enabled else None
        if (application.state.ops_records.enabled or payload.query_log_notice == "v2") and not ops_ready():
            raise AdmissionError("ops_storage_unavailable", 503)
        if payload.query_log_notice == "v2" and (not principal or not principal.visitor or not principal.entrypoint):
            raise AdmissionError("invalid_admission")
        rate_check(request, "ask", subject=principal.subject if principal else None)
        try:
            await asyncio.wait_for(application.state.slots.acquire(), timeout=.1)
        except TimeoutError:
            raise HTTPException(503, "现在有几位读者正在提问，请稍等片刻再试。") from None
        try:
            reservation, initial_quota = await application.state.quota.reserve(principal)
        except BaseException:
            application.state.slots.release()
            raise

        generation_task = None
        quota_finished = False
        resources_released = False
        record_created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        record_started = time.monotonic()
        record_model = os.getenv("AI_MODEL", DEFAULT_MODEL)
        record_status = "cancelled"
        ops_record_id = None

        async def release_resources():
            nonlocal resources_released
            if resources_released:
                return
            # Both the iterator and ASGI response call this guard. Claim cleanup
            # before its first await so cancellation cannot release a slot twice.
            resources_released = True
            try:
                try:
                    if generation_task is not None:
                        if not generation_task.done():
                            generation_task.cancel()
                        await asyncio.gather(generation_task, return_exceptions=True)
                finally:
                    if reservation is not None and not quota_finished:
                        # Disconnect cancellation is persistent in Starlette.
                        # Bound protected cleanup; the durable lease is the
                        # crash/network-failure fallback, never a body cache.
                        with anyio.CancelScope(shield=True):
                            try:
                                async with asyncio.timeout(5):
                                    await application.state.quota.finish(reservation, False)
                            except (AdmissionError, TimeoutError):
                                pass
            finally:
                application.state.slots.release()
                if ops_record_id is not None:
                    # Once start was attempted, its acknowledgement may have
                    # been lost. An idempotent finish also resolves that case.
                    with anyio.CancelScope(shield=True):
                        try:
                            async with asyncio.timeout(5):
                                await application.state.ops_records.finish(ops_record_id, record_status,
                                    max(0, int((time.monotonic() - record_started) * 1000)))
                        except Exception:
                            pass
                # No identity/background/history enters the record. Enqueue
                # after releasing admission, outside the request's awaits.
                try:
                    if payload.query_log_notice == "v1" and application.state.quota.enabled and application.state.quota.ready:
                        application.state.query_records.enqueue(question=payload.question, created_at=record_created_at,
                            model=record_model, status=record_status, duration_ms=max(0, int((time.monotonic() - record_started) * 1000)))
                except Exception:
                    pass

        if payload.query_log_notice == "v2":
            try:
                record = application.state.ops_records.prepare(question=payload.question, created_at=record_created_at,
                    model=record_model, principal=principal, conversation_id=payload.conversation_id, intent=payload.intent)
                ops_record_id = record["record_id"]
                await application.state.ops_records.start(record)
            except BaseException as exc:
                if not isinstance(exc, asyncio.CancelledError):
                    record_status = "error"
                await release_resources()
                raise

        async def events():
            nonlocal generation_task, record_status
            async def settled_result(result):
                nonlocal quota_finished, record_status
                quota = await application.state.quota.finish(reservation, result["status"] == "answered")
                quota_finished = True
                record_status = result["status"]
                return {**result, "quota": quota} if quota is not None else result
            try:
                if initial_quota is not None:
                    yield sse("quota", initial_quota) + STREAM_HEARTBEAT
                clarification = attribution_clarification(payload.question, payload.context, payload.history)
                if clarification:
                    yield sse("result", await settled_result(clarification))
                    return
                yield sse("progress", {"stage": "retrieving", "message": "正在查找相关公开材料…"}) + STREAM_HEARTBEAT
                token = os.getenv("AI_BUILDER_TOKEN", "")
                semantic_query = payload.question + "\n" + payload.context
                if payload.history and len(payload.question) < 160 and re.search(r"^(那|这|它|上面|刚才|继续)|具体怎么|举个例子|能展开", payload.question):
                    semantic_query += "\n" + payload.history[-1].question
                # Start the network lookup alongside local retrieval. Readers
                # can open actual public excerpts without waiting on either AI call.
                semantic_task = asyncio.create_task(application.state.semantic.candidates(application.state.provider, token, semantic_query))
                try:
                    passages = await asyncio.to_thread(application.state.index.retrieve, payload.question, payload.context, payload.history, 12)
                    if passages:
                        yield sse("sources", {"phase": "initial", "provisional": True, "sources": [passage.source for passage in passages[:5]]})
                    yield sse("progress", {"stage": "matching", "message": "正在匹配意思相近的材料，并合并重复出处…"})
                    while not semantic_task.done():
                        done, _ = await asyncio.wait({semantic_task}, timeout=STREAM_HEARTBEAT_SECONDS)
                        if not done:
                            yield STREAM_HEARTBEAT
                    semantic_candidates = await semantic_task
                finally:
                    if not semantic_task.done():
                        semantic_task.cancel()
                    await asyncio.gather(semantic_task, return_exceptions=True)
                if semantic_candidates:
                    passages = await asyncio.to_thread(application.state.index.retrieve, payload.question, payload.context, payload.history, 12, semantic_candidates)
                yield sse("sources", {"phase": "matched", "provisional": True, "sources": [passage.source for passage in passages[:5]]})
                if await request.is_disconnected():
                    return
                if not application.state.index.documents:
                    record_status = "error"
                    yield sse("error", {"message": "公开资料暂时无法读取，请稍后再试。", "code": "context_unavailable"})
                    return
                token = os.getenv("AI_BUILDER_TOKEN", "")
                if not token:
                    yield sse("result", await settled_result(sources_only(passages, "当前没有连接 AI 模型；这里展示的是公开资料检索结果。", payload.intent)))
                    return
                if not any(not passage.discovery for passage in passages):
                    result = sources_only(passages, "这些条目只收录目录信息；可以打开原文继续阅读。", "find") if payload.intent == "find" and passages else unsupported_result(passages)
                    yield sse("result", await settled_result(result))
                    return
                cards = application.state.index.reasoning_bundle(payload.question, payload.context, payload.history, passages, semantic_candidates)
                yield sse("approach", answer_approach(payload.intent, passages, cards))
                yield sse("progress", {"stage": "thinking", "message": f"已找到 {len(passages)} 个候选片段，正在根据材料整理回答…"}) + STREAM_HEARTBEAT
                try:
                    updates = asyncio.Queue()
                    async def partial(value):
                        await updates.put({"_event": "partial", **value})
                    async def generate():
                        try:
                            return await generate_answer(application.state.provider, token, os.getenv("AI_MODEL", DEFAULT_MODEL), {**payload.model_dump(exclude={"query_log_notice", "conversation_id"}), "reasoning_cards": cards}, passages, on_progress=updates.put, on_partial=partial)
                        finally:
                            await updates.put(None)
                    generation_task = asyncio.create_task(generate())
                    while True:
                        try:
                            update = await asyncio.wait_for(updates.get(), timeout=STREAM_HEARTBEAT_SECONDS)
                        except TimeoutError:
                            yield STREAM_HEARTBEAT
                            continue
                        if update is None:
                            break
                        event = update.pop("_event", "progress")
                        yield sse(event, update) + (STREAM_HEARTBEAT if event == "partial" else "")
                    answer = await generation_task
                    result = assemble_answer(answer, passages)
                except ProviderFailure as exc:
                    result = sources_only(passages, FAILURES[exc.code], payload.intent)
                    result.update(retryable=True, failure_code=exc.code)
                if not await request.is_disconnected():
                    yield sse("result", await settled_result(result))
            except asyncio.CancelledError:
                raise
            except AdmissionError as exc:
                record_status = "error"
                yield sse("error", exc.payload())
            except Exception:
                record_status = "error"
                # Do not include exception strings: providers may echo input.
                yield sse("error", {"message": "这次处理没有完成，请稍后再试。", "code": "request_failed"})
            finally:
                await release_resources()

        return CleanupStreamingResponse(events(), on_close=release_resources, media_type="text/event-stream", headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"})

    @application.get("/{path:path}")
    async def static(path: str):
        dist = ROOT / "dist"
        target = (dist / path).resolve()
        if not target.is_relative_to(dist.resolve()):
            raise HTTPException(404, "页面不存在。")
        if target.is_file():
            # Vite names built assets by content hash, so only they may be cached for good.
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-store"
            return FileResponse(target, headers={"Cache-Control": cache})
        if path.startswith("api/") or path == "health" or Path(path).suffix:
            raise HTTPException(404, "页面不存在。")
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-store"})
        raise HTTPException(404, "前端页面尚未构建。")

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), workers=1, access_log=False)
