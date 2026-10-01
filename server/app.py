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
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .answers import DEFAULT_MODEL, ProviderFailure, assemble_answer, attribution_clarification, generate_answer, model_options, sources_only, unsupported_result
from .retrieval import ContextIndex
from .semantic import SemanticIndex

ROOT = Path(__file__).resolve().parents[1]
Intent = Literal["understand", "apply", "find"]
STREAM_HEARTBEAT_SECONDS = 5
# SSE comments keep connections active without inventing progress or exposing
# model reasoning. Padding also flushes small frames through buffering proxies.
STREAM_HEARTBEAT = ": keep-alive " + " " * 16384 + "\n\n"


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


def create_app(context_root: Path | None = None, provider_transport=None) -> FastAPI:
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
            yield

    application = FastAPI(title="Ask Lizheng", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @application.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # FastAPI's default detail echoes the invalid input. Keep both errors
        # and any upstream logging integration free of questions/history.
        return JSONResponse(status_code=422, content={"message": "请填写问题，并把问题控制在 2000 字、背景控制在 2500 字以内。", "code": "invalid_input"})

    @application.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail), "code": "rate_limited" if exc.status_code == 429 else "unavailable"}, headers=exc.headers)

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

    def rate_check(request: Request, route: str, multiplier: int = 1):
        address = request.client.host if request.client else "unknown"
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
                "model": model, "reasoning_effort": model_options(model).get("reasoning_effort"), "mode": "live" if ready else "search-only"}

    @application.get("/api/search")
    async def search(request: Request, q: str = Query(min_length=1, max_length=2000), intent: Intent = "find"):
        rate_check(request, "search", 3)
        if not q.strip():
            raise HTTPException(422, "请输入一个主题或问题。")
        passages = await asyncio.to_thread(application.state.index.retrieve, q, limit=8)
        return {"sources": [passage.source for passage in passages]}

    @application.post("/api/ask")
    async def ask(request: Request, payload: AskRequest):
        rate_check(request, "ask")
        try:
            await asyncio.wait_for(application.state.slots.acquire(), timeout=.1)
        except TimeoutError:
            raise HTTPException(503, "现在有几位读者正在提问，请稍等片刻再试。") from None

        async def events():
            generation_task = None
            try:
                clarification = attribution_clarification(payload.question, payload.context, payload.history)
                if clarification:
                    yield sse("result", clarification)
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
                    yield sse("error", {"message": "公开资料暂时无法读取，请稍后再试。", "code": "context_unavailable"})
                    return
                token = os.getenv("AI_BUILDER_TOKEN", "")
                if not token:
                    yield sse("result", sources_only(passages, "当前没有连接 AI 模型；这里展示的是公开资料检索结果。", payload.intent))
                    return
                if not any(not passage.discovery for passage in passages):
                    result = sources_only(passages, "这些条目只收录目录信息；可以打开原文继续阅读。", "find") if payload.intent == "find" and passages else unsupported_result(passages)
                    yield sse("result", result)
                    return
                cards = application.state.index.reasoning_bundle(payload.question, payload.context, payload.history, passages, semantic_candidates)
                yield sse("approach", answer_approach(payload.intent, passages, cards))
                yield sse("progress", {"stage": "thinking", "message": f"已找到 {len(passages)} 个候选片段，正在根据材料整理回答…"}) + STREAM_HEARTBEAT
                try:
                    updates = asyncio.Queue()
                    async def generate():
                        try:
                            return await generate_answer(application.state.provider, token, os.getenv("AI_MODEL", DEFAULT_MODEL), {**payload.model_dump(), "reasoning_cards": cards}, passages, on_progress=updates.put)
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
                        yield sse("progress", update)
                    answer = await generation_task
                    result = assemble_answer(answer, passages)
                except ProviderFailure as exc:
                    result = sources_only(passages, FAILURES[exc.code], payload.intent)
                    result.update(retryable=True, failure_code=exc.code)
                if not await request.is_disconnected():
                    yield sse("result", result)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not include exception strings: providers may echo input.
                yield sse("error", {"message": "这次处理没有完成，请稍后再试。", "code": "request_failed"})
            finally:
                try:
                    if generation_task is not None:
                        if not generation_task.done():
                            generation_task.cancel()
                        await asyncio.gather(generation_task, return_exceptions=True)
                finally:
                    # StreamingResponse may cancel cleanup awaits again on a
                    # disconnect. The bounded concurrency slot must still return.
                    application.state.slots.release()

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"})

    @application.get("/{path:path}")
    async def static(path: str):
        dist = ROOT / "dist"
        target = (dist / path).resolve()
        if not target.is_relative_to(dist.resolve()):
            raise HTTPException(404, "页面不存在。")
        if target.is_file():
            return FileResponse(target)
        if path.startswith("api/") or path == "health" or Path(path).suffix:
            raise HTTPException(404, "页面不存在。")
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        raise HTTPException(404, "前端页面尚未构建。")

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), workers=1, access_log=False)
