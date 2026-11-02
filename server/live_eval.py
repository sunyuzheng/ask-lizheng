"""Retired paid smoke runner; use the bounded scripts/answer_benchmark.py entrypoint."""

from __future__ import annotations

import json
from pathlib import Path


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
    raise RuntimeError("Legacy paid smoke evaluation is retired; use scripts/answer_benchmark.py with an experiment plan")


if __name__ == "__main__":
    print(json.dumps({"mode": "retired-paid-entrypoint", "network_requests": 0,
                      "replacement": "scripts/answer_benchmark.py (offline preview by default)"}))
