from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ask_lizheng_update_context", PROJECT / "scripts" / "update_context.py")
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)

OLD, NEW = "a" * 40, "b" * 40
HEAD = "c" * 40
LOCK = {"source_commit": OLD, "release_manifest_sha256": "1" * 64, "files": 10}
NEWER = {"source_commit": NEW, "release_manifest_sha256": "2" * 64, "files": 11}
SETTINGS = json.loads((PROJECT / "config" / "builder-deploy.json").read_text())
LIVE = {"model": SETTINGS["model"], "query_logging": {"enabled": SETTINGS["enable_query_log"]},
        "ops_logging": {"enabled": SETTINGS["enable_ops"]}}


@pytest.mark.parametrize("upstream, live, force, expected", [
    (OLD, LOCK, False, "current"),
    (NEW, LOCK, False, "update"),
    (OLD, None, False, "deploy"),
    (OLD, NEWER, False, "deploy"),
    (OLD, LOCK, True, "deploy"),
])
def test_decide_compares_upstream_main_and_the_live_release(upstream, live, force, expected):
    assert updater.decide(upstream, LOCK, live, force) == expected


def test_check_reads_main_and_the_live_tag(monkeypatch):
    seen = []
    monkeypatch.setattr(updater, "lock_at", lambda ref: seen.append(ref) or (LOCK if ref == "main" else None))
    monkeypatch.setattr(updater, "upstream_commit", lambda: OLD)
    assert updater.check() == "deploy" and seen == ["main", updater.LIVE_TAG]


def test_steps_without_a_credential_never_see_one(monkeypatch):
    for name in ("AI_BUILDER_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, "SYNTHETIC_PRIVATE_SENTINEL")
    env = updater.quiet_env()
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in env.values() and env["PYTHONDONTWRITEBYTECODE"] == "1"


@pytest.mark.parametrize("meta, same", [
    (LIVE, True),
    ({**LIVE, "model": "grok-4.5"}, False),
    ({**LIVE, "ops_logging": {"enabled": not SETTINGS["enable_ops"]}}, False),
    ({"model": SETTINGS["model"]}, False),
])
def test_live_settings_must_match_the_repository(meta, same):
    assert updater.same_settings(meta, SETTINGS) is same


def test_wait_live_needs_the_release_and_both_indexes(monkeypatch):
    answers = iter([{"context_release": "old"}, updater.UpdateError("asleep"),
                    {"context_release": "new", "semantic_ready": False, "model_ready": True},
                    {"context_release": "new", "semantic_ready": True, "model_ready": True}])
    def meta():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(updater, "live_meta", meta)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)
    assert updater.wait_live("new", timeout=60, interval=0)["semantic_ready"] is True
    monkeypatch.setattr(updater, "live_meta", lambda: {"context_release": "old"})
    with pytest.raises(updater.UpdateError):
        updater.wait_live("new", timeout=0, interval=0)


class Deployer:
    """Stands in for scripts/deploy_builder.py and records what the update asked of it."""
    class DeploymentError(Exception):
        pass

    SETTINGS = PROJECT / "config" / "builder-deploy.json"

    def __init__(self, calls, same_source=True):
        self.calls, self.same_source = calls, same_source

    def check_same_source(self, token):
        self.calls.append(("same-source", token))
        if not self.same_source:
            raise self.DeploymentError("lizheng.ai does not hold keys derived from this token")

    def settings_review(self, commit):
        self.calls.append(("review", commit))
        return {"expected_commit": commit}

    def deploy(self, review, token, **options):
        self.calls.append(("deploy", review["expected_commit"], options["verified_commit"]))


@pytest.fixture
def pipeline(monkeypatch):
    """run() with every outside effect replaced; returns the calls in order."""
    calls = []
    state = {"lock": dict(LOCK), "live": dict(LOCK), "updates": [], "pushes": [], "meta": LIVE, "remote": [HEAD]}
    monkeypatch.setenv("AI_BUILDER_TOKEN", "SYNTHETIC_PRIVATE_SENTINEL")
    def sh(*args, **kwargs):
        calls.append(("sh",) + args[:3])
        if args[:2] == ("git", "status"):
            return ""
        if args[:2] == ("git", "rev-parse"):
            return HEAD + "\n"
        return ""
    def update(token):
        calls.append(("update", token))
        result = state["updates"].pop(0) if state["updates"] else None
        if result:
            state["lock"] = dict(NEWER)
        return result
    def push():
        outcome = state["pushes"].pop(0) if state["pushes"] else None
        calls.append(("push",))
        if outcome:
            raise outcome
    monkeypatch.setattr(updater, "sh", sh)
    monkeypatch.setattr(updater, "update", update)
    monkeypatch.setattr(updater, "push", push)
    monkeypatch.setattr(updater, "start_over", lambda: calls.append(("start-over",)))
    monkeypatch.setattr(updater, "read_lock", lambda: state["lock"])
    monkeypatch.setattr(updater, "lock_at", lambda ref: state["live"])
    monkeypatch.setattr(updater, "remote_main", lambda: state["remote"][0] if len(state["remote"]) == 1 else state["remote"].pop(0))
    monkeypatch.setattr(updater, "live_meta", lambda: state["meta"])
    monkeypatch.setattr(updater, "wait_live", lambda release, **kwargs: calls.append(("live", release)))
    monkeypatch.setattr(updater, "summarize", lambda lines: calls.append(("summary",)))
    deployer = Deployer(calls)
    monkeypatch.setattr(updater, "module", lambda name: deployer)
    return calls, state, deployer


def names(calls):
    return [call[0] if call[0] != "sh" else " ".join(call[1:3]) for call in calls]


def test_current_release_costs_nothing_beyond_the_token_check(pipeline):
    calls, _, _ = pipeline
    assert updater.run() == "current"
    assert names(calls) == ["git status", "git rev-parse", "same-source", "update", "git rev-parse", "summary"]


def test_new_release_is_pushed_deployed_checked_live_then_tagged(pipeline):
    calls, state, _ = pipeline
    state["updates"] = [{"subject": "Add essays", "files": [10, 11], "embedded_windows": 3, "reused_windows": 97}]
    assert updater.run() == "deployed"
    assert names(calls) == ["git status", "git rev-parse", "same-source", "update", "push", "git rev-parse",
                            "review", "deploy", "live", "git push", "summary"]
    assert ("deploy", HEAD, HEAD) in calls and ("live", NEWER["release_manifest_sha256"]) in calls
    tag = next(call for call in calls if call[:3] == ("sh", "git", "push"))
    assert tag == ("sh", "git", "push", "--quiet")


def test_a_token_lizheng_ai_does_not_accept_stops_everything(pipeline):
    calls, _, deployer = pipeline
    deployer.same_source = False
    with pytest.raises(updater.UpdateError):
        updater.run()
    assert names(calls)[-1] == "same-source" and "update" not in names(calls)


def test_settings_that_differ_from_the_live_service_block_the_deploy(pipeline):
    calls, state, _ = pipeline
    state["updates"] = [{"subject": "s", "files": [10, 11], "embedded_windows": 0, "reused_windows": 1}]
    state["meta"] = {**LIVE, "model": "grok-4.5"}
    with pytest.raises(updater.UpdateError, match="settings differ"):
        updater.run()
    assert "deploy" not in names(calls) and "live" not in names(calls)


def test_a_rejected_push_starts_over_from_the_new_main(pipeline):
    calls, state, _ = pipeline
    change = {"subject": "s", "files": [10, 11], "embedded_windows": 0, "reused_windows": 1}
    state["updates"] = [change, change]
    state["pushes"] = [updater.BranchMoved()]
    assert updater.run() == "deployed"
    assert names(calls).count("update") == 2 and "start-over" in names(calls)


def test_main_moving_before_the_deploy_starts_over(pipeline):
    calls, state, _ = pipeline
    state["live"] = None
    state["remote"] = [HEAD, "d" * 40, HEAD]
    assert updater.run() == "deployed"
    assert names(calls).count("start-over") == 1 and names(calls).count("deploy") == 1


def test_an_unchanged_release_is_committed_without_a_deploy(pipeline):
    calls, state, _ = pipeline
    state["updates"] = [{"subject": "Fix a typo outside the release", "files": [10, 10], "embedded_windows": 0, "reused_windows": 1}]
    state["live"] = dict(NEWER)
    assert updater.run() == "updated"
    assert "push" in names(calls) and "deploy" not in names(calls)


def test_a_forced_run_deploys_the_current_release(pipeline):
    calls, _, _ = pipeline
    assert updater.run(force_deploy=True) == "deployed"
    assert "deploy" in names(calls) and "push" not in names(calls)


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def test_commit_holds_only_context_data_under_the_owner(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "data/context").mkdir(parents=True)
    (repo / "data/semantic").mkdir(parents=True)
    (repo / "data/context-lock.json").write_text("{}")
    (repo / "README.md").write_text("readme")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-qm", "start")
    monkeypatch.setattr(updater, "PROJECT", repo)
    (repo / "data/context/新材料.md").write_text("公开正文")
    (repo / "data/semantic/vectors.f32").write_bytes(b"\0" * 8)
    updater.commit_data("Add essays", OLD, LOCK, NEWER, {"embedded_windows": 1, "reused_windows": 2})
    assert git(repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>").strip() == "Yuzheng Sun <sunyuzheng@gmail.com>|Yuzheng Sun <sunyuzheng@gmail.com>"
    assert "Update context to Open Context aaaaaaa" in git(repo, "log", "-1", "--format=%B")
    (repo / "README.md").write_text("changed")
    (repo / "data/context/另一份.md").write_text("公开正文")
    with pytest.raises(updater.UpdateError, match="outside the context data"):
        updater.commit_data("s", OLD, LOCK, NEWER, {"embedded_windows": 0, "reused_windows": 1})
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "2"
