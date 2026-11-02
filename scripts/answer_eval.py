#!/usr/bin/env python3
"""Historical deterministic checks; the legacy paid runner is retired.

Use scripts/answer_benchmark.py and docs/EXPERIMENTS.md for bounded experiments.
Existing published evaluation-status.json remains a historical result.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / "tests/fixtures/answer-eval.json"
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


def judge(*args, **kwargs):
    raise RuntimeError("Legacy paid judging is retired; use the guarded answer_benchmark.py")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Historical diagnostics only; no paid requests.")
    parser.add_argument("--cases", nargs="*")
    args = parser.parse_args(argv)
    cases = [c for c in json.loads(FIXTURE.read_text())["cases"] if not args.cases or c["id"] in args.cases]
    print(json.dumps({"mode": "retired-paid-entrypoint", "cases": len(cases), "network_requests": 0,
                      "replacement": "scripts/answer_benchmark.py (offline preview by default; see docs/EXPERIMENTS.md)"}))


if __name__ == "__main__":
    main()
