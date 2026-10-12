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
VIEW = re.compile(r"立正(本人)?(认为|主张|觉得|相信|建议|强调|提到|指出|说|的(观点|立场|判断|看法))")
QUOTE = re.compile(r"「([^」]{6,})」|“([^”]{6,})”")
ORIGINAL_WORDS = re.compile(r"原话|逐字|写道|引用|名言|他说|她说|立正说|作者说|文中说|说过|一句话")
CRITERIA = ["relevance", "attribution", "faithfulness", "time", "limitations", "readability"]

JUDGE_PROMPT = """你在评估“问问立正”的一个回答。问问立正根据立正公开的文章、视频和对谈回答问题。立正、孙煜征、Yuzheng Sun、课代表立正是同一个人：出处的作者或说话人写 Yuzheng Sun 或立正，都是他本人。要求：
立正的观点只能来自他本人的材料；嘉宾、主持人或其他人的话归他们本人；引用必须忠实于给出的出处；要意识到材料的时间；材料不够时说明局限；语言直接好读。
下面给你问题、这类问题“好回答”应该包含什么、实际回答（每段后面是它引用的出处编号）以及出处（编号、标题、说话人或作者、日期、摘录）。
按1到5打分（5最好），只根据给出的出处判断事实，不用你自己的知识补充：
relevance 是否回答了问题；attribution 归属是否准确（尤其不能把别人的话说成立正的观点，也不能把立正的提问当成他的观点）；
faithfulness 内容和引文是否忠实于出处、有没有编造；time 是否注意材料的时间、没有把旧信息说成现在；limitations 材料不够时有没有说明；readability 是否直接好读。
对“材料里没有”的问题，正确地说明没有并且不编造，算高分。回答按设计只转述、不写原话；引号可能用来标节目标题或强调，不要求逐字。出处里的“某人在本期的陈述”片段，是这一位被确认为陈述的句子按时间汇集，可以作为这位说话人的观点。
输出JSON：{"scores": {"relevance": n, "attribution": n, "faithfulness": n, "time": n, "limitations": n, "readability": n},
"problems": ["具体问题，指出哪句话"], "verdict": "good" 或 "acceptable" 或 "poor"}"""


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
        "method": "Local app instance on the committed context; every case through /api/ask; deterministic checks plus a rubric judged by a different model. Real model calls, not a guarantee for other questions.",
        "summary": summary, "cases": rows}, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
