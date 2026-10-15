#!/usr/bin/env python3
"""Builder Space's side of 问问立正's daily check; a failure is the alarm (GitHub mails the owner).

lizheng.ai checks what it can read (questions answered, the public feed, files, backups; its
shared/ask-health.ts). What needs Builder's token is checked here, from the builder environment:

- spend against the account's cost ceiling (鸭哥 raised it to $1000 on 2026-10-05 and asked us to
  watch it ourselves; the platform sends no warning, and at the ceiling every answer stops);
- the end of the free hosting (one year from the first deployment) and the deployment's state;
- whether the live service answers from Open Context's latest release (the update workflow mails
  its own failures; this catches an update that never ran).

  AI_BUILDER_TOKEN=... python scripts/builder_health.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://space.ai-builders.com/backend/v1"
SERVICE = "ask-lizheng"
META_URL = "https://ask-lizheng.ai-builders.space/api/meta"
UPSTREAM_MAIN = "https://api.github.com/repos/sunyuzheng/lizheng-open-context/commits/main"
MIN_BUDGET_USD = 100
HOSTING_NOTICE_DAYS = 60
RELEASE_GRACE_HOURS = 3


def get(url: str, token: str = "", timeout: float = 60) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "ask-lizheng-health"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        value = json.loads(response.read(2_000_000))
    if not isinstance(value, dict):
        raise ValueError("unexpected response")
    return value


def when(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def check(token: str, now: datetime) -> tuple[list[str], list[str]]:
    problems, notes = [], []
    usage = get(API + "/usage/summary", token)
    ceiling, spent = float(usage["cost_ceiling_usd"]), float(usage["lifetime_cost_usd"])
    notes.append(f"AI spend ${spent:.2f} of the ${ceiling:.0f} ceiling")
    if usage.get("over_cost_ceiling") or ceiling - spent < MIN_BUDGET_USD:
        problems.append(f"Builder 账号的 AI 花费已到 ${spent:.0f}，上限 ${ceiling:.0f}；到上限后所有回答都会停。请鸭哥调高上限。")

    deployment = get(API + "/deployments/" + SERVICE, token)
    notes.append(f"deployment {deployment.get('status')}")
    if deployment.get("status") not in {"HEALTHY", "SLEEPING"}:
        problems.append(f"回答服务的部署状态是 {deployment.get('status')}，不是正常运行。")
    expires = deployment.get("hosting_expires_at")
    if expires:
        days = (when(expires) - now).days
        notes.append(f"free hosting until {expires[:10]} ({days} days)")
        if days < HOSTING_NOTICE_DAYS:
            problems.append(f"回答服务的免费托管 {expires[:10]} 到期（还有{days}天），要请鸭哥延期或换地方。")

    meta = get(META_URL, timeout=90)
    if meta.get("model_ready") is not True or meta.get("semantic_ready") is not True:
        problems.append("回答服务报告模型或语义索引没就绪。")
    upstream = get(UPSTREAM_MAIN)
    latest, at = upstream["sha"], when(upstream["commit"]["committer"]["date"])
    notes.append(f"answers from Open Context {str(meta.get('context_commit', ''))[:7]}, latest {latest[:7]}")
    if meta.get("context_commit") != latest and (now - at).total_seconds() > RELEASE_GRACE_HOURS * 3600:
        problems.append(f"Open Context 最新一版（{latest[:7]}，{at:%m-%d %H:%M} UTC 推上去的）还没进问问立正；看 ask-lizheng 的 Update context 运行记录。")
    return problems, notes


def main() -> int:
    token = os.environ.get("AI_BUILDER_TOKEN", "")
    if not token:
        print("AI_BUILDER_TOKEN is not set.", file=sys.stderr)
        return 1
    try:
        problems, notes = check(token, datetime.now(timezone.utc))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        problems, notes = [f"体检本身没跑完：{type(error).__name__}"], []
    lines = ["Builder health " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), *("- " + note for note in notes),
             *(["", "要看一下："] + ["- " + problem for problem in problems] if problems else [])]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(text)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
