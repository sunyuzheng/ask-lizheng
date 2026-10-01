"""Synthetic smoke evaluation; prints only result quality and public citations.

Run with an explicitly supplied AI_BUILDER_TOKEN environment variable. No
credential, request payload, conversation, or provider response is saved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from .answers import ProviderFailure, assemble_answer, attribution_clarification, generate_answer, unsupported_result
from .retrieval import ContextIndex
from .semantic import SemanticIndex

CASES = [
    ("learning-prefill", "我做出了几个 AI 项目，怎么知道自己是真的学会了？", "apply", ""),
    ("work-value-prefill", "用 AI 效率变高了，为什么我的工作价值没变？", "understand", ""),
    ("demo-prefill", "有一个能跑的 demo，怎么判断值不值得继续做？", "apply", ""),
    ("media-prefill", "想开始做自媒体，最应该先想清楚什么？", "understand", ""),
    ("quality-prefill", "立正说的良质是什么意思，和 AI 有什么关系？", "understand", ""),
    ("source-finder", "帮我找立正关于职业选择和个人价值的文章与视频。", "find", ""),
    ("scope-tension", "Don't build 是不是说别做学习项目了？", "understand", ""),
    ("paraphrase", "跟着助手完成了东西，但换个需求就不会了", "apply", "希望能自己诊断一个小工具的问题，不打算手写每一行代码。"),
    ("career-application", "我想转行，但不知道怎么证明自己有能力，应该先准备什么？", "apply", ""),
    ("ai-judgment", "我用 AI 写了很多东西，但感觉没有形成自己的判断，怎么办？", "apply", ""),
    ("guest-attribution", "某位嘉宾在访谈中对未来投资收益有什么保证？", "understand", ""),
    ("out-of-corpus", "今年的世界杯冠军是谁？", "understand", ""),
]


async def evaluate(root: Path, only: str = "") -> bool:
    token = os.getenv("AI_BUILDER_TOKEN", "")
    if not token:
        raise SystemExit("Set AI_BUILDER_TOKEN in the process environment to run live evaluation.")
    index = ContextIndex(root)
    index.load()
    semantic = SemanticIndex(root, index.documents)
    semantic.load()
    model = os.getenv("AI_MODEL", "gpt-5")
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(85, connect=10)) as client:
        for name, question, intent, context in CASES:
            if only and name != only:
                continue
            start = time.monotonic()
            clarification = attribution_clarification(question, context)
            candidates = await semantic.candidates(client, token, question + " " + context) if semantic.ready and not clarification else []
            passages = index.retrieve(question, context, semantic_candidates=candidates) if not clarification else []
            try:
                if clarification:
                    result = clarification
                elif not any(not passage.discovery for passage in passages):
                    result = unsupported_result(passages)
                else:
                    answer = await generate_answer(client, token, model, {"question": question, "context": context, "intent": intent, "history": [], "reasoning_cards": index.reasoning_bundle(question, context, [], passages, semantic_candidates=candidates)}, passages)
                    result = assemble_answer(answer, passages)
                output = {"case": name, "seconds": round(time.monotonic() - start, 2), "semantic_ready": semantic.ready, "status": result["status"],
                          "summary": result["summary"], "sections": result["sections"],
                          "sources": [{key: source.get(key) for key in ["id", "title", "author", "evidence_role", "url", "timecode"]} for source in result["sources"]],
                          "clarifying_questions": result["clarifying_questions"], "limitations": result["limitations"]}
                expected = {"unsupported"} if name == "out-of-corpus" else {"clarify", "unsupported"} if name == "guest-attribution" else {"answered", "clarify"}
                passed = result["status"] in expected
                output["status_check_passed"] = passed
                results.append(passed)
            except ProviderFailure as exc:
                output = {"case": name, "seconds": round(time.monotonic() - start, 2), "failure": exc.code}
                results.append(False)
            print(json.dumps(output, ensure_ascii=False), flush=True)
    return bool(results) and all(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-root", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "context")
    parser.add_argument("--case", choices=[name for name, *_ in CASES], default="")
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(evaluate(args.context_root, args.case)) else 1)
