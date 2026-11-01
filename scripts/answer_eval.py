#!/usr/bin/env python3
"""Real-model answer quality, measured locally on the committed context.

Every question in tests/fixtures/answer-eval.json goes through the service's own /api/ask route
in a local app instance (no quota, no records, no analytics, nothing published). Each answer is
then checked two ways:

- deterministic: the status matches what the case expects; words presented as someone's original
  words (「」 or “” after 原话、说过 and the like) appear in the excerpts the answer cites; a sentence that gives 立正 a view cites at least one source
  attributed to him; a case that needs his own sources has at least one.
- judged: a different model scores relevance, attribution, faithfulness to the sources, time
  awareness, stated limits and readability from 1 to 5, against what the case says a good answer
  contains, and names concrete problems.

Writes docs/evaluation-status.json. Needs AI_BUILDER_TOKEN and makes real model calls (about two
dozen answers and as many judgements). Run: python scripts/answer_eval.py [--cases id ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / "tests/fixtures/answer-eval.json"
OUTPUT = ROOT / "docs/evaluation-status.json"
JUDGE_MODEL = "gpt-5"
URL = "https://space.ai-builders.com/backend/v1/chat/completions"
OWN_ORIGINS = {"yuzheng-published-text", "yuzheng-spoken-source"}
QUOTE = re.compile(r"「([^」]{6,})」|“([^”]{6,})”")
ORIGINAL_WORDS = re.compile(r"原话|逐字|写道|引用|名言|他说|她说|立正说|作者说|文中说|说过|一句话")
from server.answer_contract import CRITERIA as CONTRACT_CRITERIA, JUDGE_CRITERIA
from server.names import NameIndex

CRITERIA = list(JUDGE_CRITERIA)
_table = ROOT / "data/context/catalog/guest-names.json"
_host_names = NameIndex(json.loads(_table.read_text())).speaker_names("yuzheng")
VIEW = re.compile(r"(?:" + "|".join(re.escape(name) for name in _host_names) + r")(本人)?(认为|主张|觉得|相信|建议|强调|提到|指出|说|的(观点|立场|判断|看法))", re.I)
JUDGE_PROMPT = (
    "你在评估问问立正的局部回答回归。人物目录中的同一人称呼：" + "、".join(_host_names)
    + "。对以下合同逐项1到5分：\n"
    + json.dumps({key: CONTRACT_CRITERIA[key]["instruction"] for key in CRITERIA}, ensure_ascii=False)
    + "\n这里只给实际检索片段，没有独立完整资料金标准。可以判断是否编造，但不能凭片段不足认定检索成功、全库没有或任务完成。"
      "本检查不能衡量资料漏检；完整效果评测请用answer_benchmark.py。回答按产品默认转述。"
      "只输出JSON，scores包含上述六个ID的整数1到5；problems为具体问题字符串数组；verdict为good、acceptable或poor。"
)



def norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", text)).lower()


def sse_result(response) -> tuple[dict | None, list[str]]:
    event, data, result, stages = None, [], None, []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line.strip() and event:
            payload = json.loads("".join(data)) if data else {}
            if event == "progress":
                stages.append(payload.get("stage", ""))
            if event == "result":
                result = payload
            event, data = None, []
    return result, stages


def answer_text(result: dict) -> list[tuple[str, list[str]]]:
    """(text, cited source ids) for the summary and every section."""
    rows = [(result.get("summary", ""), re.findall(r"\[S(\d+)\]", result.get("summary", "")))]
    for section in result.get("sections", []):
        rows.append((section.get("body", ""), [s.removeprefix("S") for s in section.get("source_ids", [])]
                     + re.findall(r"\[S(\d+)\]", section.get("body", ""))))
    return rows


def deterministic(case: dict, result: dict) -> dict:
    sources = {s["id"].removeprefix("S"): s for s in result.get("sources", [])}
    checks = {"status": result.get("status"), "status_ok": case["expect"] in ("any", result.get("status"))}
    quotes, unverified = 0, []
    view_sentences, unsupported_views = 0, []
    for text, cited in answer_text(result):
        cited_sources = [sources[c] for c in cited if c in sources] or list(sources.values())
        excerpts = norm(" ".join(s.get("excerpt", "") for s in cited_sources))
        titles = {norm(s.get("title", "")) for s in sources.values()}
        for match in QUOTE.finditer(text):
            quote = match.group(1) or match.group(2)
            prefix = text[max(0, match.start() - 12):match.start()]
            if norm(quote) in titles or not ORIGINAL_WORDS.search(prefix):
                continue  # a title, or emphasis: the service does not present it as anyone's words
            quotes += 1
            if norm(quote) not in excerpts:
                unverified.append(quote[:60])
        for sentence in re.findall(r"[^。！？]+[。！？]?", text):
            if not VIEW.search(sentence):
                continue
            ids = re.findall(r"\[S(\d+)\]", sentence) or cited
            refs = [sources[i] for i in ids if i in sources]
            if not refs:
                continue
            view_sentences += 1
            if not any(r.get("content_origin") in OWN_ORIGINS or r.get("speaker_id") == "yuzheng" for r in refs):
                unsupported_views.append(sentence.strip()[:80])
    checks.update(quotes=quotes, unverified_quotes=unverified, yuzheng_view_sentences=view_sentences,
                  views_without_own_source=unsupported_views,
                  own_sources=sum(1 for s in sources.values() if s.get("content_origin") in OWN_ORIGINS or s.get("speaker_id") == "yuzheng"),
                  sources=len(sources))
    if case.get("own") and result.get("status") == "answered":
        checks["own_ok"] = checks["own_sources"] > 0
    return checks


def judge(client: httpx.Client, token: str, case: dict, result: dict) -> dict:
    parts = [f"[摘要] {result.get('summary', '')}"]
    for section in result.get("sections", []):
        parts.append(f"[{section.get('heading', '')}] {section.get('body', '')} 出处：{','.join(section.get('source_ids', []))}")
    if result.get("limitations"):
        parts.append(f"[局限] {result['limitations']}")
    sources = [f"{s['id']}｜{s.get('title', '')}｜说话人或作者：{s.get('speaker_name') or s.get('author') or s.get('speaker_id', '')}"
               f"｜{s.get('date') or s.get('published_at', '')}｜{s.get('excerpt', '')}" for s in result.get("sources", [])]
    content = (f"问题：{case['question']}\n读者补充的处境：{case.get('context', '无')}\n好回答应包含：{case['good']}\n"
               f"回答状态：{result.get('status')}\n\n回答：\n" + "\n".join(parts) + "\n\n出处：\n" + "\n".join(sources))
    payload = {"model": JUDGE_MODEL, "reasoning_effort": "low", "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": content}]}
    for attempt in range(3):
        try:
            response = client.post(URL, json=payload, headers={"Authorization": "Bearer " + token}, timeout=180)
            response.raise_for_status()
            value = json.loads(response.json()["choices"][0]["message"]["content"])
            if set(CRITERIA) <= set(value.get("scores", {})):
                return value
        except Exception as error:  # retried, then recorded as missing
            last = str(error)
        time.sleep(2 + attempt * 3)
    return {"error": last if "last" in locals() else "invalid judgement"}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", nargs="*")
    args = parser.parse_args()
    token = os.environ.get("AI_BUILDER_TOKEN")
    if not token:
        raise SystemExit("AI_BUILDER_TOKEN is not set")
    for name in ("ASK_OPS_ENABLED", "UPSTASH_REDIS_REST_URL", "KV_REST_API_URL"):
        os.environ.pop(name, None)
    from fastapi.testclient import TestClient
    from server.answers import DEFAULT_MODEL
    from server.app import create_app
    cases = [c for c in json.loads(FIXTURE.read_text())["cases"] if not args.cases or c["id"] in args.cases]
    rows = []
    app = create_app(context_root=ROOT / "data/context")
    with TestClient(app) as client, httpx.Client() as judge_client:
        meta = client.get("/api/meta").json()
        for case in cases:
            payload = {"question": case["question"], "intent": case["intent"], "context": case.get("context", ""), "history": []}
            started = time.monotonic()
            with client.stream("POST", "/api/ask", json=payload) as response:
                result, stages = sse_result(response) if response.status_code == 200 else (None, [])
            seconds = round(time.monotonic() - started, 1)
            if result is None:
                rows.append({"id": case["id"], "error": f"no result (HTTP {response.status_code})", "seconds": seconds})
                print(case["id"], "no result", flush=True)
                continue
            checks = deterministic(case, result)
            verdict = judge(judge_client, token, case, result) if result.get("status") != "clarify" else {"skipped": "clarify"}
            rows.append({"id": case["id"], "intent": case["intent"], "question": case["question"], "seconds": seconds,
                         "checks": checks, "judge": verdict, "summary": result.get("summary", "")[:200]})
            print(case["id"], result.get("status"), seconds, verdict.get("verdict"), verdict.get("scores"), flush=True)
            time.sleep(6)  # stay under the local per-minute limit
    judged = [r for r in rows if isinstance(r.get("judge", {}).get("scores"), dict)]
    averages = {c: round(sum(r["judge"]["scores"][c] for r in judged) / len(judged), 2) for c in CRITERIA} if judged else {}
    summary = {
        "cases": len(rows), "judged": len(judged), "average_scores": averages,
        "verdicts": {v: sum(1 for r in judged if r["judge"].get("verdict") == v) for v in ("good", "acceptable", "poor")},
        "status_mismatches": [r["id"] for r in rows if not r.get("checks", {}).get("status_ok", False)],
        "unverified_quotes": sum(len(r.get("checks", {}).get("unverified_quotes", [])) for r in rows),
        "quotes": sum(r.get("checks", {}).get("quotes", 0) for r in rows),
        "views_without_own_source": sum(len(r.get("checks", {}).get("views_without_own_source", [])) for r in rows),
        "missing_own_sources": [r["id"] for r in rows if r.get("checks", {}).get("own_ok") is False],
        "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else None,
    }
    OUTPUT.write_text(json.dumps({
        "schema_version": 2, "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "answer_model": os.getenv("AI_MODEL", DEFAULT_MODEL), "judge_model": JUDGE_MODEL,
        "context_commit": meta.get("context_commit", ""), "context_release": meta.get("context_release", ""),
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "method": "Legacy local regression through /api/ask, judged only against retrieved excerpts. Does not measure missed corpus evidence or full task completion; use answer_benchmark.py for independent paired evaluation.",
        "summary": summary, "cases": rows}, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
