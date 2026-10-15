#!/usr/bin/env python3
"""Start the built image as Builder Space runs it, in 256 MB, and stop a release that cannot start
there or comes close to the limit.

Builder Space gives the service a 256 MB container. Until 2026-10-15 the service held the whole corpus
and reached about that much; a larger release would have failed to start, and nothing checked before
it was deployed. CI and the context update run this on every image: it must answer /health and
/api/meta with its semantic index within two minutes, answer a few searches, and keep the service's
peak resident memory (VmHWM, which leaves out the page cache the kernel can reclaim) under CEILING_MB.

  python scripts/check_image_memory.py [image]
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

LIMIT = "256m"
CEILING_MB = 200
PORT = 18765
NAME = "ask-lizheng-memory-check"
SEARCHES = ("跟着 AI 做出工具，换个需求就不会了，怎样练自己的判断？", "大厂还是创业", "AI coding 零基础怎么开始", "日活不涨该拉新还是留存")


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode:
        raise SystemExit(f"docker {args[0]} failed: {result.stderr.strip()[-500:]}")
    return result.stdout.strip()


def get(path: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as response:
        return json.loads(response.read())


def main() -> int:
    image = sys.argv[1] if len(sys.argv) > 1 else "ask-lizheng"
    docker("rm", "-f", NAME, check=False)
    docker("run", "-d", "--name", NAME, "--memory", LIMIT, "--memory-swap", LIMIT, "-p", f"127.0.0.1:{PORT}:8000", image)
    try:
        started, ready = time.monotonic(), False
        while time.monotonic() - started < 120:
            state = json.loads(docker("inspect", NAME))[0]["State"]
            if state.get("OOMKilled") or not state.get("Running"):
                print(docker("logs", "--tail", "20", NAME, check=False))
                raise SystemExit(f"The image stopped while starting in {LIMIT} (out of memory: {state.get('OOMKilled')}).")
            try:
                ready = get("/health").get("context_ready") is True and get("/api/meta").get("semantic_ready") is True
            except (OSError, ValueError):
                ready = False
            if ready:
                break
            time.sleep(2)
        if not ready:
            print(docker("logs", "--tail", "20", NAME, check=False))
            raise SystemExit("The image did not answer with its context and semantic index within two minutes.")
        print(f"ready after {time.monotonic() - started:.0f}s")
        for question in SEARCHES:
            sources = get("/api/search?q=" + urllib.parse.quote(question), timeout=30)["sources"]
            if not sources:
                raise SystemExit(f"No source found for {question!r}.")
        status = docker("exec", NAME, "cat", "/proc/1/status")
        peak = next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmHWM:")) / 1024
        current = next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:")) / 1024
        print(f"peak resident memory {peak:.0f} MB, now {current:.0f} MB, of {LIMIT}; ceiling {CEILING_MB} MB")
        if peak > CEILING_MB:
            raise SystemExit(f"The service reached {peak:.0f} MB, over the {CEILING_MB} MB ceiling of a {LIMIT} container.")
        return 0
    finally:
        docker("rm", "-f", NAME, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
