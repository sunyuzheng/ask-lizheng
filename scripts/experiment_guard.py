"""Decision-bound, private, cumulative limits for development API calls.

This is not used by production answers or approved corpus maintenance. Currency
reservations are estimates; provider billing can lag and include other traffic.
Request, payload and output-setting limits are enforced before dispatch.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = Path.home() / ".local/state/ask-lizheng/experiments"
COST_URL = "https://space.ai-builders.com/backend/v1/usage/summary"
PAID_PATHS = {"/backend/v1/chat/completions", "/backend/v1/embeddings"}


class ExperimentStop(RuntimeError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def private_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def positive(value, *, integer=False):
    return (type(value) is int and value > 0) if integer else (
        type(value) in (int, float) and math.isfinite(value) and value > 0)


class Experiment:
    def __init__(self, path: Path, *, root=None, state_root=None):
        self.root = Path(ROOT if root is None else root).resolve()
        path = Path(path).resolve()
        if not path.is_relative_to(self.root / ".local"):
            raise ValueError("Experiment plans must stay under this project's .local directory")
        self.plan = json.loads(path.read_text())
        p = self.plan
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(p.get("id", ""))):
            raise ValueError("Use a stable, short experiment id")
        for key in ("decision", "uncertainty", "decision_signal", "stop_condition"):
            if not isinstance(p.get(key), str) or not p[key].strip():
                raise ValueError("Experiment plan needs " + key)
        for key in ("max_cases", "max_repeats", "max_requests", "max_request_bytes", "max_output_tokens"):
            if not positive(p.get(key), integer=True):
                raise ValueError("Experiment plan needs a positive integer " + key)
        if not positive(p.get("budget_usd")) or not positive(p.get("estimated_total_usd")):
            raise ValueError("Declare the experiment budget and expected cost")
        if p["estimated_total_usd"] > p["budget_usd"]:
            raise ValueError("Expected cost exceeds the experiment budget")
        prices = p.get("reservation_usd_per_request", {})
        if not isinstance(prices, dict) or not prices or not all(isinstance(k, str) and positive(v) for k, v in prices.items()):
            raise ValueError("Declare conservative request reservations for every allowed model")
        self.signature = digest(p)
        self.directory = Path(STATE_ROOT if state_root is None else state_root) / p["id"]
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.directory / "budget.json"
        private_json(self.directory / (self.signature + ".plan.json"), p)

    def scope(self, case_ids, repeats=1, *, fresh=False, retry=False):
        p = self.plan
        if not case_ids or not positive(repeats, integer=True) or len(case_ids) > p["max_cases"] or repeats > p["max_repeats"]:
            raise ValueError("Requested scope exceeds the experiment plan")
        allowed = p.get("case_ids")
        if allowed:
            if not isinstance(allowed, list) or not all(isinstance(v, str) for v in allowed) or not set(case_ids) <= set(allowed):
                raise ValueError("Requested cases are outside the experiment plan")
        elif not str(p.get("why_full_scope", "")).strip():
            raise ValueError("Name the cases, or explain why the full scope is necessary")
        if repeats > 1 and not str(p.get("why_repeats", "")).strip():
            raise ValueError("Repeated sampling needs a decision-relevant variance question")
        if (fresh or retry) and not str(p.get("new_signal", "")).strip():
            raise ValueError("Fresh or retried calls need an explicit incremental signal")

    @contextmanager
    def locked(self):
        fd = os.open(self.directory / "budget.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {
                "schema_version": 1, "plan_sha256": self.signature, "requests": 0,
                "reserved_usd": 0, "baseline_account_cost_usd": None, "events": []}
            if state["plan_sha256"] != self.signature:
                raise ExperimentStop("This experiment id already has a different plan; its budget cannot reset")
            yield state
            private_json(self.state_path, state)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def reserve(self, payload: dict, body: bytes, account_cost: float, stage: str):
        p = self.plan
        model = payload.get("model")
        if model not in p["reservation_usd_per_request"]:
            self.deny("Model is outside the experiment plan")
        if len(body) > p["max_request_bytes"]:
            self.deny("Input exceeds the declared request-size limit; nothing was truncated")
        if "messages" in payload:
            limit = payload.get("max_tokens", payload.get("max_completion_tokens"))
            if not positive(limit, integer=True) or limit > p["max_output_tokens"]:
                self.deny("An explicit output limit within the experiment plan is required")
        reason = None
        with self.locked() as state:
            if state.get("stopped"):
                raise ExperimentStop(state["stopped"])
            if state["baseline_account_cost_usd"] is None:
                state["baseline_account_cost_usd"] = account_cost
            state["latest_account_cost_usd"] = account_cost
            reservation = p["reservation_usd_per_request"][model]
            if state["requests"] >= p["max_requests"]:
                reason = "Declared request limit reached"
            elif state["reserved_usd"] + reservation > p["budget_usd"] + 1e-9:
                reason = "Declared currency reservations exhausted"
            elif account_cost - state["baseline_account_cost_usd"] + reservation > p["budget_usd"] + 1e-9:
                reason = "Reported account spend reached the experiment budget"
            if reason:
                state["stopped"] = reason
            else:
                state["requests"] += 1
                state["reserved_usd"] = round(state["reserved_usd"] + reservation, 9)
                state["events"].append({"stage": stage, "model": model, "request_sha256": hashlib.sha256(body).hexdigest(),
                                        "bytes": len(body), "reservation_usd": reservation})
        if reason:
            raise ExperimentStop(reason)

    def stop(self, reason):
        with self.locked() as state:
            state["stopped"] = reason

    def deny(self, reason):
        self.stop(reason)
        raise ExperimentStop(reason)

    def receipt(self):
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {
            "plan_sha256": self.signature, "requests": 0, "reserved_usd": 0}

    def conclude(self, outcome: str, evidence: str):
        if outcome not in {"adopt", "reject", "inconclusive"} or not evidence.strip():
            raise ValueError("Record adopt/reject/inconclusive and the signal supporting that decision")
        with self.locked() as state:
            state["conclusion"] = {"outcome": outcome, "evidence": evidence}
            state["stopped"] = "Experiment concluded: " + outcome


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, experiment, token, stage, *, inner=None, meter=None):
        self.experiment, self.token, self.stage = experiment, token, stage
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.meter = meter or httpx.AsyncClient(timeout=10)
        self.owns_meter = meter is None

    async def handle_async_request(self, request):
        if request.method != "POST" or request.url.scheme != "https" or request.url.host != "space.ai-builders.com" or request.url.port not in (None, 443) or request.url.path not in PAID_PATHS:
            self.experiment.deny("Paid experiments may only call the declared Builder model endpoints")
        if not self.token or request.headers.get("authorization") != "Bearer " + self.token:
            self.experiment.deny("The spend monitor and model request must use the same credential")
        try:
            payload = json.loads(request.content)
            if not isinstance(payload, dict):
                raise ValueError("not an object")
        except (ValueError, TypeError):
            self.experiment.deny("A bounded JSON model request is required")
        try:
            response = await self.meter.get(COST_URL, headers={"Authorization": "Bearer " + self.token}, follow_redirects=False)
            response.raise_for_status()
            cost = response.json()["lifetime_cost_usd"]
            if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                raise ValueError("invalid cost")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            self.experiment.deny("Cannot verify account spend; no model request was sent")
        self.experiment.reserve(payload, request.content, cost, self.stage)
        response = await self.inner.handle_async_request(request)
        if response.status_code in {401, 402, 403, 429}:
            self.experiment.stop("Authentication, billing or rate-limit failure; stop instead of retrying")
            await response.aclose()
            raise ExperimentStop("Authentication, billing or rate-limit failure; no further calls allowed")
        return response

    async def aclose(self):
        await self.inner.aclose()
        if self.owns_meter:
            await self.meter.aclose()


class NoNetworkTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        raise ExperimentStop("No reusable result for this work unit; offline reuse never calls a provider")


def add_paid_args(parser, *, reuse=False):
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Make paid calls; default is an offline scope preview")
    parser.add_argument("--experiment-plan", type=Path, help="Private decision, scope, cost and stopping plan")
    if reuse:
        mode.add_argument("--reuse-only", action="store_true", help="Reuse matching frozen work without any network access or credentials")
        parser.add_argument("--reuse-report", type=Path)
        parser.add_argument("--fresh", action="store_true", help="Regenerate despite an existing result; plan must name the new signal")
        parser.add_argument("--retry-errors", action="store_true", help="Retry failed work only; plan must name the new signal")


def execution(args, case_ids, repeats=1):
    if not getattr(args, "execute", False) or not getattr(args, "experiment_plan", None):
        raise ValueError("Paid calls require --execute and --experiment-plan; use the default offline preview first")
    experiment = Experiment(args.experiment_plan)
    experiment.scope(case_ids, repeats, fresh=getattr(args, "fresh", False), retry=getattr(args, "retry_errors", False))
    return experiment


def preview(args, case_ids, repeats=1):
    if getattr(args, "reuse_only", False):
        return False
    if getattr(args, "execute", False):
        execution(args, case_ids, repeats)
        return False
    print(json.dumps({"mode": "offline-preview", "cases": list(case_ids), "repeats": repeats,
                      "work_units": len(case_ids) * repeats, "network_requests": 0,
                      "output_exists": bool(getattr(args, "output", None) and args.output.exists()),
                      "next": "Only execute if this scope can change the stated decision; provide a private experiment plan."}, ensure_ascii=False))
    return True


def require_guarded(client):
    if not isinstance(getattr(client, "_transport", None), (BudgetTransport, NoNetworkTransport, httpx.MockTransport)):
        raise ValueError("Use a budgeted experiment client; only MockTransport may bypass paid-call limits")


@asynccontextmanager
async def paid_client(experiment, token, stage):
    if experiment is not None and not token:
        raise ValueError("AI_BUILDER_TOKEN is required for execution; --reuse-only needs no credential")
    transport = BudgetTransport(experiment, token, stage) if experiment is not None else NoNetworkTransport()
    async with httpx.AsyncClient(transport=transport, timeout=180, follow_redirects=False) as client:
        yield client


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read or close an experiment; never calls a model")
    parser.add_argument("action", choices=("status", "conclude"))
    parser.add_argument("--experiment-plan", type=Path, required=True)
    parser.add_argument("--outcome", choices=("adopt", "reject", "inconclusive"))
    parser.add_argument("--evidence", default="")
    args = parser.parse_args()
    experiment = Experiment(args.experiment_plan)
    if args.action == "conclude":
        experiment.conclude(args.outcome, args.evidence)
    print(json.dumps(experiment.receipt(), ensure_ascii=False))
