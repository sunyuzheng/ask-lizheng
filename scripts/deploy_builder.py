#!/usr/bin/env python3
"""Review an exact deployment; mutate only after matching approval digest.

The approval argument is an execution boundary, not a substitute for obtaining
the user's explicit approval of the displayed payload and destination.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

DEPLOYMENTS_URL = "https://space.ai-builders.com/backend/v1/deployments"
DEFAULT_REPO = "https://github.com/sunyuzheng/ask-lizheng"
SERVICE = "ask-lizheng"
PUBLIC_URL = "https://ask-lizheng.ai-builders.space/"
WORKFLOW_STATES = {"queued", "deploying"}
TERMINAL_STATES = {"HEALTHY", "SLEEPING", "UNHEALTHY", "DEGRADED", "ERROR"}


class DeploymentError(Exception):
    pass


class RemoteError(DeploymentError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"Remote request failed (HTTP {status})." if status else "Remote request failed or timed out.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the provider Authorization header to a redirect target.
        return None


def repository_parts(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(repo_url)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment or parsed.params:
        raise DeploymentError("Repository must be a public HTTPS github.com owner/repository URL without credentials or extra parameters.")
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", parsed.path)
    if not match or match.group(1) in {".", ".."} or match.group(2) in {".", ".."}:
        raise DeploymentError("Repository URL must identify exactly one GitHub repository.")
    return match.group(1), match.group(2)


def validate_payload(payload: dict) -> None:
    if set(payload) != {"repo_url", "service_name", "branch", "port", "env_vars", "streaming_log_timeout_seconds"}:
        raise DeploymentError("Deployment payload has unsupported fields.")
    repository_parts(payload["repo_url"])
    if not re.fullmatch(r"[a-z0-9-]{3,32}", str(payload["service_name"])) or payload["service_name"] != SERVICE:
        raise DeploymentError("This script deploys only the ask-lizheng service.")
    if payload["branch"] != "main" or payload["port"] != 8000 or payload["streaming_log_timeout_seconds"] != 60:
        raise DeploymentError("Branch, port, or log timeout differs from this app's reviewed deployment contract.")
    if payload["env_vars"] != {"AI_MODEL": "grok-4-fast"}:
        # Do not repeat rejected values: they may contain a credential.
        raise DeploymentError("Only the approved, non-secret AI_MODEL configuration is allowed in env_vars.")


def build_review(repo_url: str = DEFAULT_REPO, expected_commit: str = "") -> dict:
    if expected_commit and not re.fullmatch(r"[a-fA-F0-9]{40}", expected_commit):
        raise DeploymentError("Expected commit must be a full 40-character Git SHA.")
    payload = {
        "repo_url": repo_url, "service_name": SERVICE, "branch": "main", "port": 8000,
        "env_vars": {"AI_MODEL": "grok-4-fast"}, "streaming_log_timeout_seconds": 60,
    }
    validate_payload(payload)
    return {
        "destination": DEPLOYMENTS_URL,
        "audience": "Public internet at " + PUBLIC_URL,
        "payload": payload,
        "expected_commit": expected_commit.lower() or None,
    }


def approval_digest(review: dict) -> str:
    return hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def request_json(method: str, url: str, payload: dict | None = None, token: str = "", timeout: float = 20) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "ask-lizheng-deployment-review"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise DeploymentError("Remote response exceeded the safe size limit.")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise DeploymentError("Remote response was not an object.")
            return data
    except urllib.error.HTTPError as exc:
        # The response body and exception string may echo credentials or input.
        raise RemoteError(exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RemoteError() from None
    except (ValueError, UnicodeError):
        raise DeploymentError("Remote response could not be decoded.") from None


def verify_repository(review: dict) -> str:
    payload = review["payload"]
    owner, name = repository_parts(payload["repo_url"])
    api = "https://api.github.com/repos/" + quote(owner, safe="") + "/" + quote(name, safe="")
    repo = request_json("GET", api)
    if repo.get("private") is not False or repo.get("visibility", "public") != "public":
        raise DeploymentError("Deployment requires a public repository.")
    returned_url = repo.get("html_url", "")
    if repository_parts(returned_url) != (owner, name):
        raise DeploymentError("Remote repository identity did not match the reviewed URL.")
    if repo.get("archived") or repo.get("disabled"):
        raise DeploymentError("Remote repository is archived or disabled.")
    branch = request_json("GET", api + "/branches/" + quote(payload["branch"], safe=""))
    if branch.get("name") != payload["branch"]:
        raise DeploymentError("Remote branch did not match the reviewed branch.")
    commit = (branch.get("commit") or {}).get("sha", "").lower()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise DeploymentError("Remote branch did not report a verifiable commit.")
    if review["expected_commit"] and commit != review["expected_commit"]:
        raise DeploymentError("Remote branch differs from the approved commit; no deployment was sent.")
    return commit


def check_service_binding(payload: dict, token: str) -> None:
    try:
        existing = request_json("GET", DEPLOYMENTS_URL + "/" + SERVICE, token=token)
    except RemoteError as exc:
        if exc.status == 404:
            return
        raise
    if existing.get("service_name") != SERVICE or repository_parts(existing.get("repo_url", "")) != repository_parts(payload["repo_url"]):
        raise DeploymentError("The service is already bound to a different repository; no deployment was sent.")


def redact_build_logs(logs: str, token: str) -> str:
    logs = logs.replace(token, "[redacted]") if token else logs
    safe = []
    for line in logs.splitlines():
        if re.search(r"token|api.?key|password|secret|credential|authorization|bearer\s|\bsk[-_][A-Za-z0-9_-]{10,}", line, re.I):
            safe.append("[redacted sensitive build log line]")
        else:
            safe.append(line[:1000])
    return "\n".join(safe)[:16000]


def show_build_logs(token: str) -> None:
    try:
        result = request_json("GET", DEPLOYMENTS_URL + "/" + SERVICE + "/logs?log_type=build&timeout=5", token=token, timeout=15)
        if result.get("log_type") == "build":
            logs = redact_build_logs(str(result.get("logs", "")), token)
            if logs:
                print(logs, file=sys.stderr)
    except DeploymentError:
        print("Build logs were unavailable.", file=sys.stderr)


def deploy(review: dict, token: str, poll_seconds: float = 5, max_wait_seconds: float = 900, build_logs: bool = False, *, verified_commit: str = "") -> str:
    validate_payload(review["payload"])
    if not re.fullmatch(r"[a-f0-9]{40}", review.get("expected_commit") or ""):
        raise DeploymentError("Executing deployment requires an approved full expected commit; no deployment was sent.")
    if not verified_commit:
        verified_commit = verify_repository(review)
    if verified_commit != review["expected_commit"]:
        raise DeploymentError("Remote branch differs from the approved commit; no deployment was sent.")
    check_service_binding(review["payload"], token)
    print("Verified public main commit: " + verified_commit, file=sys.stderr)
    response = request_json("POST", review["destination"], review["payload"], token=token, timeout=100)
    deadline = time.monotonic() + max_wait_seconds
    last_status = None
    while True:
        if response.get("service_name") != SERVICE:
            raise DeploymentError("Deployment response did not match the approved service.")
        status = response.get("status")
        if status not in WORKFLOW_STATES | TERMINAL_STATES:
            raise DeploymentError("Platform returned an unrecognized deployment state.")
        if status != last_status:
            print("Deployment status: " + status, file=sys.stderr)
            last_status = status
        if status in TERMINAL_STATES:
            if build_logs:
                show_build_logs(token)
            if status not in {"HEALTHY", "SLEEPING"}:
                raise DeploymentError("Deployment reached an unhealthy terminal state; inspect build logs.")
            deployed_commit = response.get("git_commit_id")
            if review["expected_commit"] and deployed_commit and deployed_commit.lower() != review["expected_commit"]:
                raise DeploymentError("Healthy deployment reported a different commit than approved.")
            if review["expected_commit"] and not deployed_commit:
                print("Platform did not report a deployment commit; the branch was verified before dispatch only.", file=sys.stderr)
            public_url = response.get("public_url") or PUBLIC_URL
            parsed = urlparse(public_url)
            if parsed.scheme != "https" or parsed.netloc != "ask-lizheng.ai-builders.space" or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise DeploymentError("Platform public URL did not match the approved public destination.")
            return public_url
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if build_logs:
                show_build_logs(token)
            raise DeploymentError("Deployment is still pending after the bounded wait; use read-only status checks.")
        time.sleep(min(poll_seconds, remaining, 10))
        response = request_json("GET", DEPLOYMENTS_URL + "/" + SERVICE, token=token, timeout=min(20, max(1, remaining)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--expected-commit", default="")
    approval = parser.add_mutually_exclusive_group()
    approval.add_argument("--dry-run", action="store_true", help="Print the exact review; never read credentials or make network calls.")
    approval.add_argument("--approved-sha", help="Digest of the exact payload explicitly approved by the user.")
    parser.add_argument("--show-build-logs", action="store_true", help="Read redacted build logs only; runtime logs are never requested.")
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--max-wait-seconds", type=float, default=900)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.poll_seconds <= 10 or not 30 <= args.max_wait_seconds <= 1200:
            raise DeploymentError("Polling must be 1–10 seconds, with a bounded wait of 30–1200 seconds.")
        review = build_review(args.repo_url, args.expected_commit)
        digest = approval_digest(review)
        print(json.dumps({"mode": "approved-deploy" if args.approved_sha else "dry-run", "execution_ready": bool(review["expected_commit"]), "review": review, "approval_sha256": digest}, ensure_ascii=False, indent=2))
        if not args.approved_sha:
            return 0
        if not re.fullmatch(r"[a-f0-9]{64}", args.approved_sha) or not hmac.compare_digest(args.approved_sha, digest):
            raise DeploymentError("Approval digest mismatch; no credentials were read and no deployment was sent.")
        if not review["expected_commit"]:
            raise DeploymentError("Approval requires --expected-commit before execution; no credentials were read and no deployment was sent.")
        # Public repository verification occurs before reading any credential.
        verified_commit = verify_repository(review)
        token = os.environ.get("AI_BUILDER_TOKEN", "")
        if not token:
            raise DeploymentError("AI_BUILDER_TOKEN is required in the process environment; no deployment was sent.")
        public_url = deploy(review, token, args.poll_seconds, args.max_wait_seconds, args.show_build_logs, verified_commit=verified_commit)
        print(public_url)
        return 0
    except DeploymentError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
