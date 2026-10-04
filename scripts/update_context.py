#!/usr/bin/env python3
"""Keep 问问立正 on the latest Open Context release, with no one in the loop.

Pushing Open Context main approves its content for 问问立正 (Yuzheng's decision, 2026-10-04). This
brings that release here, rebuilds the semantic index (unchanged windows keep their vectors), runs
the tests, commits to main, deploys Builder with config/builder-deploy.json and waits until the live
service answers from the new release. It changes no code and stops at the first failed check; the
workflow run then fails and GitHub emails the owner. .github/workflows/update-context.yml runs it.

  check            print action=current|update|deploy, what run would do (no credential, no checkout)
  run [--deploy]   do it; --deploy deploys main even when the live service already answers from it
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Open Context's search code is imported to plan the index; keep its bytecode out of the copy.
sys.dont_write_bytecode = True

PROJECT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/sunyuzheng/lizheng-open-context"
ORIGIN = "https://github.com/sunyuzheng/ask-lizheng"
REPOSITORY = "sunyuzheng/ask-lizheng"
LOCK = "data/context-lock.json"
# Moved to each deployed commit once the live service answers from its release.
LIVE_TAG = "context-live"
META_URL = "https://ask-lizheng.ai-builders.space/api/meta"
AUTHOR = ("Yuzheng Sun", "sunyuzheng@gmail.com")
DATA = ("data/context", LOCK, "data/semantic")
SHA = re.compile(r"[a-f0-9]{40}")


class UpdateError(Exception):
    pass


class BranchMoved(Exception):
    """main changed under this run: start again from the new main."""


def module(name: str):
    spec = importlib.util.spec_from_file_location("ask_lizheng_" + name, PROJECT / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def quiet_env() -> dict:
    """The environment for every step that needs no credential, including Open Context's own code."""
    env = {key: value for key, value in os.environ.items() if key not in {"AI_BUILDER_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}}
    return {**env, "PYTHONDONTWRITEBYTECODE": "1"}


def sh(*args: str, cwd: Path | None = None, timeout: float = 600) -> str:
    try:
        result = subprocess.run(args, cwd=cwd or PROJECT, env=quiet_env(), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UpdateError(f"Timed out: {' '.join(args[:3])}") from None
    if result.returncode != 0:
        sys.stderr.write((result.stdout + result.stderr)[-4000:] + "\n")
        raise UpdateError(f"Failed: {' '.join(args[:3])}")
    return result.stdout


def upstream_commit() -> str:
    fields = sh("git", "ls-remote", UPSTREAM, "refs/heads/main", timeout=60).split()
    if len(fields) != 2 or not SHA.fullmatch(fields[0]):
        raise UpdateError("Open Context main could not be read.")
    return fields[0]


def remote_main() -> str:
    fields = sh("git", "ls-remote", ORIGIN, "refs/heads/main", timeout=60).split()
    if len(fields) != 2 or not SHA.fullmatch(fields[0]):
        raise UpdateError("main could not be read.")
    return fields[0]


def lock_at(ref: str) -> dict | None:
    """The context lock at a branch or tag of this repository on GitHub; None when the ref is absent."""
    result = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/contents/{LOCK}?ref={ref}", "-H", "Accept: application/vnd.github.raw+json"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        if "HTTP 404" in result.stderr:
            return None
        raise UpdateError(f"{LOCK} at {ref} could not be read from GitHub.")
    return json.loads(result.stdout)


def read_lock() -> dict:
    return json.loads((PROJECT / LOCK).read_text(encoding="utf-8"))


def needs_deploy(main_lock: dict, live_lock: dict | None, force_deploy: bool = False) -> bool:
    return force_deploy or not live_lock or live_lock.get("release_manifest_sha256") != main_lock.get("release_manifest_sha256")


def decide(upstream: str, main_lock: dict, live_lock: dict | None, force_deploy: bool = False) -> str:
    if main_lock.get("source_commit") != upstream:
        return "update"
    return "deploy" if needs_deploy(main_lock, live_lock, force_deploy) else "current"


def check(force_deploy: bool = False) -> str:
    main_lock = lock_at("main")
    if main_lock is None:
        raise UpdateError("main has no context lock.")
    return decide(upstream_commit(), main_lock, lock_at(LIVE_TAG), force_deploy)


def update(token: str) -> dict | None:
    """Copy, index and test the latest release, then commit it; None when main already has it."""
    before = read_lock()
    upstream = upstream_commit()
    if before.get("source_commit") == upstream:
        return None
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "open-context"
        sh("git", "clone", "--quiet", "--depth", "1", "--branch", "main", UPSTREAM, str(source), timeout=600)
        commit = sh("git", "-C", str(source), "rev-parse", "HEAD").strip()
        subject = sh("git", "-C", str(source), "log", "-1", "--format=%s").strip()
        # Open Context's own release checks first; the copy then verifies every file against the manifest.
        sh(sys.executable, "scripts/validate_release.py", cwd=source)
        sh(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q", cwd=source)
        sh(sys.executable, "scripts/sync_context.py", "--source", str(source))
    after = read_lock()
    if after.get("source_commit") != commit:
        raise UpdateError("The copy does not name the Open Context commit it came from.")
    builder = module("build_semantic_index")
    try:
        metadata, inputs = builder.plan()
        built = asyncio.run(builder.build(metadata, inputs, token, builder.reusable(PROJECT / "data/semantic")))
    except builder.SemanticIndexError as exc:
        raise UpdateError("Semantic index: " + str(exc)) from None
    sh(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", timeout=1200)
    sh(sys.executable, "data/context/scripts/validate_release.py")
    if not commit_data(subject, commit, before, after, built):
        return None
    return {"open_context": commit, "subject": subject, "files": [before.get("files"), after.get("files")],
            "embedded_windows": built["embedded_windows"], "reused_windows": built["reused_windows"]}


def commit_data(subject: str, commit: str, before: dict, after: dict, built: dict) -> bool:
    """Commit the copy, its lock and the index as the owner. Nothing else may have changed."""
    sh("git", "add", "--all", "--", *DATA)
    staged = [path for path in sh("git", "diff", "--cached", "--name-only", "--no-renames", "-z").split("\0") if path]
    stray = sh("git", "diff", "--name-only", "-z") + sh("git", "ls-files", "--others", "--exclude-standard", "-z")
    if stray.strip("\0") or any(not path.startswith(DATA) for path in staged):
        raise UpdateError("The update touched files outside the context data; nothing was committed.")
    if not staged:
        return False
    message = (f"Update context to Open Context {commit[:7]}\n\n{subject}\n\n"
               f"Release files {before.get('files')} → {after.get('files')}; embedded {built['embedded_windows']} new windows, "
               f"kept {built['reused_windows']}.\nAutomated by .github/workflows/update-context.yml.\n")
    sh("git", "-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}", "commit", "--quiet", "-m", message)
    return True


def push() -> None:
    result = subprocess.run(["git", "push", "--quiet", "origin", "HEAD:refs/heads/main"], cwd=PROJECT, env=quiet_env(),
                            capture_output=True, text=True, timeout=600)
    if result.returncode == 0:
        return
    if re.search(r"rejected|fetch first|non-fast-forward", result.stderr):
        raise BranchMoved()
    raise UpdateError("Push to main failed.")


def start_over() -> None:
    sh("git", "fetch", "--quiet", "--depth", "1", "origin", "main")
    sh("git", "reset", "--quiet", "--hard", "FETCH_HEAD")


def live_meta() -> dict:
    request = urllib.request.Request(META_URL, headers={"Accept": "application/json", "User-Agent": "ask-lizheng-context-update"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read(1_000_000))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise UpdateError("The live service did not answer.") from None
    if not isinstance(data, dict):
        raise UpdateError("The live service answered with something unexpected.")
    return data


def same_settings(meta: dict, settings: dict) -> bool:
    """What the live service shows of its settings agrees with config/builder-deploy.json."""
    return (meta.get("model") == settings["model"]
            and (meta.get("query_logging") or {}).get("enabled") is settings["enable_query_log"]
            and (meta.get("ops_logging") or {}).get("enabled") is settings["enable_ops"])


def wait_live(release: str, timeout: float = 1200, interval: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        try:
            meta = live_meta()
            if meta.get("context_release") == release and meta.get("semantic_ready") is True and meta.get("model_ready") is True:
                return meta
        except UpdateError:
            pass
        if time.monotonic() >= deadline:
            raise UpdateError("Deployed, but the live service does not answer from the new release with its semantic index.")
        time.sleep(interval)


def summarize(lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as summary:
            summary.write(text)


def run(force_deploy: bool = False, attempts: int = 3) -> str:
    token = os.environ.get("AI_BUILDER_TOKEN", "")
    if not token:
        raise UpdateError("AI_BUILDER_TOKEN is not set.")
    if sh("git", "status", "--porcelain").strip():
        raise UpdateError("The checkout has local changes; run this in a clean clone of main.")
    if sh("git", "rev-parse", "HEAD").strip() != remote_main():
        raise UpdateError("The checkout is not at main.")
    deployer = module("deploy_builder")
    try:
        # Before anything is spent: the service would fail every signed call with any other token.
        deployer.check_same_source(token)
        for _ in range(attempts):
            try:
                updated = update(token)
                if updated:
                    push()
                head = sh("git", "rev-parse", "HEAD").strip()
                main_lock = read_lock()
                if not needs_deploy(main_lock, lock_at(LIVE_TAG), force_deploy):
                    summarize(["问问立正 already answers from Open Context " + str(main_lock.get("source_commit", ""))[:7] + "."])
                    return "updated" if updated else "current"
                settings = json.loads(deployer.SETTINGS.read_text(encoding="utf-8"))
                if not same_settings(live_meta(), settings):
                    raise UpdateError("The live service's settings differ from config/builder-deploy.json; nothing was deployed.")
                if remote_main() != head:
                    raise BranchMoved()
                review = deployer.settings_review(head)
                # Verified just above with git, which no API rate limit can turn away.
                deployer.deploy(review, token, poll_seconds=10, max_wait_seconds=1200, build_logs=True, verified_commit=head)
                wait_live(main_lock["release_manifest_sha256"])
                sh("git", "push", "--quiet", "--force", "origin", f"{head}:refs/tags/{LIVE_TAG}")
                lines = [f"问问立正 now answers from Open Context {str(main_lock.get('source_commit', ''))[:7]} (deployed {head[:7]})."]
                if updated:
                    lines.append(f"{updated['subject']} — release files {updated['files'][0]} → {updated['files'][1]}; "
                                 f"embedded {updated['embedded_windows']} new windows, kept {updated['reused_windows']}.")
                summarize(lines)
                return "deployed"
            except BranchMoved:
                start_over()
        raise UpdateError("main kept moving during the update; the next run will try again.")
    except deployer.DeploymentError as exc:
        raise UpdateError("Deploy: " + str(exc)) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "run"))
    parser.add_argument("--deploy", action="store_true", help="Deploy main even when the live service already answers from it")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print("action=" + check(args.deploy))
        else:
            print("result=" + run(args.deploy))
        return 0
    except UpdateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
