#!/usr/bin/env python3
"""Private repeated answer generation and blinded paired judging; never calls the public API."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import evidence_eval as evidence
from scripts import experiment_guard as experiments

URL = "https://space.ai-builders.com/backend/v1/chat/completions"
JUDGE_MODEL = "gpt-5"


def judge_evidence(run: dict, saved_input: dict) -> tuple[dict, dict]:
    """Put actual excerpts in the public answer's ID space, with no added body text."""
    saved_input = run.get("actual_input", saved_input)
    public = run["answer"]
    public_sources = public.get("sources", [])
    public_ids = [source["id"] for source in public_sources]
    if len(set(public_ids)) != len(public_ids):
        raise ValueError("Public answer has duplicate source IDs")
    marks = re.findall(r"\[(S\d+)\]", json.dumps({key: value for key, value in public.items() if key != "sources"}, ensure_ascii=False))
    marks += [source_id for section in public.get("sections", []) for source_id in section.get("source_ids", [])]
    if set(marks) - set(public_ids):
        raise ValueError("Public answer cites a source absent from its public sources")
    route = saved_input["route"]
    if "validated_model_answer" not in run:
        if route == "generate":
            raise ValueError("Generated answer is missing its validated model answer")
        # Deterministic navigation/clarification has no answering-model input.
        # Its public source snapshots are the evidence actually displayed.
        return {"answer": copy.deepcopy(public), "actual_model_input": {
                    "route": route, "sources": copy.deepcopy(public_sources)}}, {
                    "basis": "public_result_sources", "public_to_retrieval": {}, "unreferenced_to_retrieval": {}}

    actual = saved_input["sources"]
    actual_ids = [source["id"] for source in actual]
    if len(set(actual_ids)) != len(actual_ids):
        raise ValueError("Actual model input has duplicate source IDs")
    by_id = {source["id"]: source for source in actual}
    snapshots = saved_input["source_snapshots"]
    public_snapshots = saved_input.get("public_source_snapshots", snapshots)
    snapshot_ids = [source.get("id") for source in snapshots]
    if len(set(snapshot_ids)) != len(snapshot_ids) or set(actual_ids) != set(snapshot_ids):
        raise ValueError("Saved source snapshots do not match the actual input source IDs")
    snapshot_by_id = {source["id"]: source for source in snapshots}
    for source_id, source in by_id.items():
        snapshot = snapshot_by_id[source_id]
        if any(source.get(key, "") != snapshot.get(key, "") for key in ("title", "author", "date", "excerpt")):
            raise ValueError("Saved source snapshot differs from the actual model excerpt or metadata")
    stable = ("title", "url", "date", "excerpt", "author", "source_type", "public_copy_url",
              "data_layer", "speaker_id", "fragment_id", "source_snapshot_sha256")
    public_to_retrieval = {}
    for source in public_sources:
        if any(key not in source for key in ("title", "url", "excerpt")):
            raise ValueError("Public source lacks a traceable source identity")
        matches = [snapshot["id"] for snapshot in public_snapshots
                   if all(snapshot.get(key, "") == source.get(key, "")
                          for key in stable if key in source)]
        if len(matches) != 1 or matches[0] in public_to_retrieval.values():
            raise ValueError("Public source identity is missing or ambiguous in the saved model input")
        public_to_retrieval[source["id"]] = matches[0]
    retrieval_to_public = {original: public_id for public_id, original in public_to_retrieval.items()}
    aligned, unused = [], {}
    for source in actual:
        original = source["id"]
        label = retrieval_to_public.get(original)
        if label is None:
            label = "U" + str(len(unused) + 1)
            unused[label] = original
        aligned.append({**copy.deepcopy(source), "id": label})
    return {"answer": copy.deepcopy(public), "actual_model_input": {"route": route, "sources": aligned}}, {
                "basis": "exact_saved_source_snapshot_identity", "public_to_retrieval": public_to_retrieval,
                "unreferenced_to_retrieval": unused}


def require_private_output(path: Path) -> None:
    if not path.resolve().is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("Full benchmark answers and excerpts must stay under this repository's .local directory")


def prior_report(args):
    """Keep evidence immutable: resume into a new report, never clobber a paid run."""
    if args.output.exists():
        raise ValueError("Output already exists; preserve it and use a new --output with --reuse-report")
    path = getattr(args, "reuse_report", None)
    if path is None:
        return None, None
    require_private_output(path)
    raw = path.read_bytes()
    return json.loads(raw), evidence.digest(raw)


def reuse_generation(impl, cached, current_input, passages, origin_sha, model):
    row = copy.deepcopy(cached)
    selected = passages
    if cached.get("actual_input"):
        ids = {s["id"] for s in cached["actual_input"]["sources"]}
        selected = [p for p in passages if p.source["id"] in ids]
        if {p.source["id"] for p in selected} != ids:
            raise ValueError("Cached selection no longer exists in the current candidates")
        messages = impl.answers.answer_payload(model, current_input["request"], selected)["messages"] if selected else []
        if messages != cached["actual_input"]["model_messages"]:
            raise ValueError("Cached answering input changed; a version number alone cannot establish reuse")
        row["actual_input"] = {**current_input, "route": "generate" if selected else "selection-empty",
            "model_messages": messages, "sources": [impl.answers.model_evidence(p) for p in selected],
            "source_snapshots": [p.evidence for p in selected], "public_source_snapshots": [p.source for p in selected]}
    if "validated_model_answer" in cached:
        answer = impl.answers.ModelAnswer.model_validate(cached["validated_model_answer"])
        impl.answers.normalize_source_bindings(answer, selected)
        impl.answers.validate_answer(answer, selected)
        row["answer"] = impl.answers.assemble_answer(answer, selected)
    elif current_input["route"] == "generate" and selected:
        raise ValueError("Cached model answer lacks validated source bindings")
    row.update(reused=True, reused_from_sha256=origin_sha)
    return row


def answer_state(case: dict, metrics: dict, result: dict) -> str:
    """Keep retrieval failure separate from a negative case's expected absence."""
    if case["reference_ids"]:
        if not metrics["any_reference_hit"]:
            return "retrieval_missed_and_abstained" if result.get("status") == "unsupported" else "expected_evidence_missing"
        return "available_evidence_declined" if result.get("status") == "unsupported" else "expected_evidence_available"
    return "corpus_absence_expected" if case["expect"] == "unsupported" else "no_gold_reference_required"


def generation_summary(runs: list[dict]) -> dict:
    successful = [run for run in runs if "answer" in run]
    by_case = defaultdict(list)
    for run in successful:
        by_case[run["id"]].append(run)
    variation = {}
    for case_id, rows in by_case.items():
        lengths = [len(json.dumps(row["answer"], ensure_ascii=False)) for row in rows]
        measured = [row["seconds"] for row in rows if not row.get("reused")]
        variation[case_id] = {"runs": len(rows), "statuses": dict(Counter(row["answer"]["status"] for row in rows)),
                              "characters_mean": statistics.mean(lengths),
                              "characters_stdev": statistics.pstdev(lengths),
                              "generation_seconds_mean": statistics.mean(row["seconds"] for row in rows),
                              "new_measurements": len(measured), "new_generation_seconds_mean": statistics.mean(measured) if measured else None}
    return {"runs": len(runs), "completed": len(successful), "reused_runs": sum(bool(r.get("reused")) for r in runs),
            "provider_or_validation_failures": [{"id": row["id"], "repeat": row["repeat"], "error": row["error"]}
                                                 for row in runs if "error" in row],
            "status_mismatches": [{"id": row["id"], "repeat": row["repeat"], "status": row["answer"]["status"]}
                                  for row in successful if not row["status_ok"]],
            "evidence_states": dict(Counter(row["evidence_state"] for row in successful)), "case_variation": variation}


async def generate(args, token: str, client=None) -> dict:
    require_private_output(args.output)
    if not 1 <= args.repeats <= 10 or not 1 <= args.concurrency <= 8:
        raise ValueError("Use 1-10 repeats and 1-8 concurrent calls")
    impl = evidence.implementation(args.implementation)
    fixture = evidence.load_fixture(args.fixture, impl.root / "data/context")
    cases = evidence.select_cases(fixture, args.split, args.cases)
    if client is not None:
        experiments.require_guarded(client)
    experiment = (experiments.execution(args, [c["id"] for c in cases], args.repeats)
                  if client is None and not getattr(args, "reuse_only", False) else None)
    index = impl.retrieval.ContextIndex(impl.root / "data/context", require_lock=True)
    index.load()
    if fixture.get("context_commit") != index.source_revision:
        raise ValueError("Fixture and pinned context commits differ")
    semantic = index.semantic_index()
    if not semantic.ready:
        raise ValueError("Pinned semantic index is not ready")
    cached, vector_meta = await evidence.vectors(impl, cases, args.vectors_cache)
    model = args.model or impl.answers.DEFAULT_MODEL
    backup = impl.answers.BACKUP_MODELS.get(model)
    prepared, inputs, metrics = {}, {}, {}
    for case in cases:
        case_id = case["id"]
        value = evidence.prepare_case(impl, index, semantic, case, cached[case_id])
        prepared[case_id] = value
        metrics[case_id] = evidence.evidence_metrics(impl, index, case, fixture["refs"], value)
        request = {"question": case["question"], "intent": case["intent"], "context": case.get("context", ""),
                   "history": case.get("history", []), "reasoning_cards": value["cards"]}
        # This is the exact production payload builder, never a reconstructed selection of sources.
        payload = impl.answers.answer_payload(model, request, value["passages"]) if value["route"] == "generate" else None
        inputs[case_id] = {"route": value["route"], "request": request,
                           "model_messages": payload["messages"] if payload else [],
                           "sources": [impl.answers.model_evidence(p) for p in value["passages"]],
                           "source_snapshots": [p.evidence for p in value["passages"]],
                           "public_source_snapshots": [p.source for p in value["passages"]], "metrics": metrics[case_id]}
    report = {"schema_version": 1, "kind": "answer-generation", "implementation": str(impl.root),
              "implementation_sha256": evidence.implementation_hash(impl.root),
              "fixture_sha256": evidence.digest(args.fixture.read_bytes()), "context_commit": index.source_revision,
              **vector_meta, "answer_model": model, "backup_model": backup, "repeats": args.repeats,
              "concurrency": args.concurrency, "inputs": inputs, "runs": [],
              "method": "Direct local production retrieval/generation functions; no public API, quota, archive, or raw provider reasoning. Cached retrieval is reused for repeated answer sampling."}
    previous, previous_sha = prior_report(args)
    if previous and not getattr(args, "fresh", False) and any(previous.get(k) != report[k] for k in (
            "implementation_sha256", "answer_model", "backup_model")):
        raise ValueError("Cached implementation/model changed; narrow the experiment and explicitly justify --fresh")
    previous_rows = {(r["id"], r["repeat"]): r for r in previous.get("runs", [])} if previous else {}
    semaphore = asyncio.Semaphore(args.concurrency)
    stopped = asyncio.Event()

    async def one(case, repeat, provider):
        async with semaphore:
            case_id, started = case["id"], time.monotonic()
            value = prepared[case_id]
            row = {"id": case_id, "group": case["group"], "split": case["split"], "category": case["category"], "repeat": repeat}
            cached = previous_rows.get((case_id, repeat))
            try:
                if (cached and not getattr(args, "fresh", False) and value["route"] == "generate"
                        and previous.get("inputs", {}).get(case_id, {}).get("model_messages") != inputs[case_id]["model_messages"]):
                    raise experiments.ExperimentStop("Cached generation input changed; assess the new signal before explicitly requesting --fresh")
                reusable = (cached and value["route"] == "generate" and previous.get("implementation_sha256") == report["implementation_sha256"]
                            and all(previous.get(k) == report[k] for k in ("answer_model", "backup_model"))
                            and previous.get("inputs", {}).get(case_id, {}).get("model_messages") == inputs[case_id]["model_messages"]
                            and not getattr(args, "fresh", False))
                if reusable and "answer" in cached:
                    row = reuse_generation(impl, cached, inputs[case_id], value["passages"], previous_sha, model)
                    row.update(group=case["group"], split=case["split"], category=case["category"],
                               status_ok=case["expect"] in {"any", row["answer"]["status"]})
                elif reusable and "error" in cached and not getattr(args, "retry_errors", False):
                    row = {**copy.deepcopy(cached), "reused": True, "reused_from_sha256": previous_sha}
                elif stopped.is_set():
                    row.update(error="experiment_stopped", skipped=True)
                elif value["route"] == "generate":
                    passages = value["passages"]
                    kwargs = {}
                    if impl.selection is not None:
                        deadline = time.monotonic() + impl.answers.ANSWER_BUDGET_SECONDS
                        if reusable and cached.get("actual_input") and getattr(args, "retry_errors", False):
                            wanted = {s["id"] for s in cached["actual_input"]["sources"]}
                            passages = [p for p in passages if p.source["id"] in wanted]
                            messages = impl.answers.answer_payload(model, inputs[case_id]["request"], passages)["messages"] if passages else []
                            if messages != cached["actual_input"]["model_messages"]:
                                raise ValueError("Cached selection input changed")
                            row["selection_reused"] = True
                        else:
                            passages = await impl.selection.select_evidence(provider, token, model, inputs[case_id]["request"], passages)
                        kwargs["deadline"] = deadline
                        row["selection_seconds"] = round(time.monotonic() - started, 3)
                        row["actual_input"] = {**inputs[case_id], "route": "generate" if passages else "selection-empty",
                            "sources": [impl.answers.model_evidence(p) for p in passages],
                            "source_snapshots": [p.evidence for p in passages], "public_source_snapshots": [p.source for p in passages],
                            "model_messages": impl.answers.answer_payload(model, inputs[case_id]["request"], passages)["messages"] if passages else []}
                    if passages:
                        answer = await impl.answers.generate_answer(provider, token, model, inputs[case_id]["request"], passages, backup=backup, **kwargs)
                        row["validated_model_answer"] = answer.model_dump()
                    else:
                        answer = impl.selection.empty_answer()
                    result = impl.answers.assemble_answer(answer, passages)
                    selected_value = {**value, "passages": passages}
                    row["evidence_state"] = answer_state(case, evidence.evidence_metrics(impl, index, case, fixture["refs"], selected_value), result)
                else:
                    result = copy.deepcopy(value["result"])
                if "answer" not in row and "error" not in row:
                    row.update(answer=result, status_ok=case["expect"] in {"any", result["status"]})
                    row.setdefault("evidence_state", answer_state(case, metrics[case_id], result))
                if row.get("reused") and "answer" in row:
                    wanted = {s["id"] for s in row.get("actual_input", inputs[case_id])["sources"]}
                    selected = [p for p in value["passages"] if p.source["id"] in wanted]
                    row["evidence_state"] = answer_state(case, evidence.evidence_metrics(
                        impl, index, case, fixture["refs"], {**value, "passages": selected}), row["answer"])
            except experiments.ExperimentStop as exc:
                row.update(error="experiment_stopped", skipped=True)
                report["stopped"] = str(exc)
                stopped.set()
                if experiment is not None:
                    experiment.stop(str(exc))
            except impl.answers.ProviderFailure as exc:
                row["error"] = exc.code
            except Exception as exc:
                # Exception strings can contain HTTP URLs, headers, or provider responses.
                row["error"] = "internal_" + type(exc).__name__
            if not row.get("reused"):
                row["seconds"] = round(time.monotonic() - started, 3)
            report["runs"].append(row)
            report["runs"].sort(key=lambda item: (item["id"], item["repeat"]))
            report["summary"] = generation_summary(report["runs"])
            evidence.private_write(args.output, report)
            print(json.dumps({key: row.get(key) for key in ("id", "repeat", "seconds", "error")}), flush=True)

    async def run(provider):
        await asyncio.gather(*(one(case, repeat, provider) for repeat in range(args.repeats) for case in cases))

    if client is None:
        async with experiments.paid_client(experiment, token, "generate") as provider:
            await run(provider)
    else:
        await run(client)
    report["summary"] = generation_summary(report["runs"])
    if experiment is not None:
        report["experiment"] = experiment.receipt()
    evidence.private_write(args.output, report)
    return report


def gold_evidence(fixture: dict, case: dict, context_root: Path) -> list[dict]:
    result = []
    for ref_id in case["reference_ids"]:
        ref = fixture["refs"][ref_id]
        body = evidence.source_body(context_root / ref["path"], ref.get("speaker_id"))
        excerpts = [ref["context"]] if ref.get("context") else []
        if ref.get("unit_ids"):
            units = json.loads((context_root / ref["path"]).read_text())["units"]
            wanted = set(ref["unit_ids"])
            excerpts = ["\n".join(f"[{u['speaker_id']}] {u['text']}" for u in units if u["id"] in wanted)]
        for quote in ref["quotes"]:
            anchor = evidence.normalize(quote)
            if any(anchor in evidence.normalize(text) for text in excerpts):
                continue
            position = body.find(anchor)
            excerpt = body[max(0, position - 220):position + len(anchor) + 380]
            if excerpt not in excerpts:
                excerpts.append(excerpt)
        result.append({"reference_id": ref_id, "path": ref["path"], "speaker_id": ref.get("speaker_id"),
                       "anchors": ref["quotes"], "excerpts": excerpts, "notes": ref.get("notes", "")})
    return result


def blind_order(case_id: str, repeat: int, seed: str) -> dict:
    flip = int(evidence.digest(f"{seed}:{case_id}:{repeat}".encode())[:8], 16) % 2
    return {"A": "candidate", "B": "baseline"} if flip else {"A": "baseline", "B": "candidate"}


def rubric():
    contract = importlib_contract()
    return {key: contract.CRITERIA[key]["instruction"] for key in contract.JUDGE_CRITERIA}


def importlib_contract():
    import importlib
    return importlib.import_module(evidence.implementation(ROOT).name + ".answer_contract")


def judge_prompt() -> str:
    return ("你是一次盲评的独立评审。A/B顺序已随机化，不猜哪个是新版。按同一合同逐项1至5分。\n"
            + json.dumps(rubric(), ensure_ascii=False)
            + "\n给你独立原文金标准、好回答要求，以及每个回答实际收到的证据片段和标注。判断忠实度时只按该回答实际收到的excerpt；"
              "判断任务完成和相关性时还须检查独立金标准。金标准在公开库中，但检索未提供，并不等于库中不存在。"
              "检索漏证据后诚实地说不足，可以没有编造，但不能因此把任务完成、相关性评为成功；分别报告这两件事。"
              "没有金标准且expect=unsupported的负例才标记为预期的库外问题；这也不是自动证明整个库不存在。"
              "指出归属或忠实度的严重错误时，引用具体错误句及与哪段证据冲突；没有则为空。"
              "每个回答的证据ID已依据保存的来源身份映射到该回答的公开编号。S1/S2对应这份回答的同号证据；"
              "U1/U2是模型实际收到但未作为公开来源列出的候选，也保留原始片段。不要按列表位置猜编号，更不能把U2当作S2。"
              "完整评价回答的摘要、正文、标题、限制、追问和澄清。不要输出思维过程。只输出JSON："
              '{"A":{"scores":{各合同ID:1到5},"problems":["具体问题"],'
              '"critical_errors":[{"criterion":"attribution或faithfulness","text":"具体错误句和证据"}],'
              '"task_fulfillment":"complete或partial或failed","honest_missing_input":true},'
              '"B":{同A},"preference":"A或B或tie","reason":"简短理由"}')


def validate_judgment(value: dict) -> dict:
    criteria = set(rubric())
    if value.get("preference") not in {"A", "B", "tie"} or not isinstance(value.get("reason"), str):
        raise ValueError("Invalid paired preference")
    for label in ("A", "B"):
        row = value.get(label, {})
        if (set(row.get("scores", {})) != criteria
                or any(type(score) is not int or not 1 <= score <= 5 for score in row["scores"].values())
                or row.get("task_fulfillment") not in {"complete", "partial", "failed"}
                or type(row.get("honest_missing_input")) is not bool
                or not isinstance(row.get("problems"), list) or any(not isinstance(p, str) for p in row["problems"])
                or not isinstance(row.get("critical_errors"), list)):
            raise ValueError("Invalid paired rubric response")
        for error in row["critical_errors"]:
            if error.get("criterion") not in {"attribution", "faithfulness"} or not isinstance(error.get("text"), str) or not error["text"]:
                raise ValueError("Invalid critical error")
    return value


def judge_summary(rows: list[dict], criteria: list[str]) -> dict:
    judged = [row for row in rows if "judgment" in row]
    by_case, by_group = defaultdict(list), defaultdict(list)
    for row in judged:
        by_case[row["id"]].append(row)
        by_group[row["group"]].append(row)

    def aggregate(values):
        scores = {side: {criterion: {"mean": statistics.mean(row["judgment"][side]["scores"][criterion] for row in values),
                                    "stdev": statistics.pstdev(row["judgment"][side]["scores"][criterion] for row in values)}
                         for criterion in criteria} for side in ("baseline", "candidate")}
        return {"pairs": len(values), "scores": scores,
                "preferences": dict(Counter(row["judgment"]["preference"] for row in values)),
                "critical_errors": {side: sum(len(row["judgment"][side]["critical_errors"]) for row in values)
                                    for side in ("baseline", "candidate")}}

    regressions = []
    for row in judged:
        b, c = row["judgment"]["baseline"], row["judgment"]["candidate"]
        if c["critical_errors"] and (len(c["critical_errors"]) > len(b["critical_errors"])
                                     or {e["criterion"] for e in c["critical_errors"]} - {e["criterion"] for e in b["critical_errors"]}):
            regressions.append({"id": row["id"], "repeat": row["repeat"], "errors": c["critical_errors"]})
    return {"paired_runs": len(rows), "judged_runs": len(judged),
            "missing_or_failed_judgments": [{"id": row["id"], "repeat": row["repeat"], "error": row.get("error")}
                                             for row in rows if "judgment" not in row],
            "cases": {key: aggregate(values) for key, values in sorted(by_case.items())},
            "groups": {key: aggregate(values) for key, values in sorted(by_group.items())},
            "critical_regressions": regressions,
            "case_score_deltas": {key: {criterion: statistics.mean(
                row["judgment"]["candidate"]["scores"][criterion] - row["judgment"]["baseline"]["scores"][criterion]
                for row in values) for criterion in criteria} for key, values in sorted(by_case.items())},
            "group_mean_score_deltas": {criterion: statistics.mean(
                statistics.mean(row["judgment"]["candidate"]["scores"][criterion] - row["judgment"]["baseline"]["scores"][criterion]
                                for row in values) for values in by_group.values()) for criterion in criteria} if judged else {}}


async def judge(args, token: str, client=None) -> dict:
    require_private_output(args.output)
    if not 1 <= args.concurrency <= 8:
        raise ValueError("Use 1-8 concurrent judge calls")
    baseline, candidate = json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text())
    for key in ("fixture_sha256", "context_commit", "query_vector_sha256", "answer_model", "backup_model"):
        if baseline.get(key) != candidate.get(key):
            raise ValueError("Answer reports have incompatible " + key)
    if evidence.digest(args.fixture.read_bytes()) != baseline["fixture_sha256"]:
        raise ValueError("Judge fixture changed after generation")
    fixture = evidence.load_fixture(args.fixture, ROOT / "data/context")
    cases = {case["id"]: case for case in evidence.select_cases(fixture, args.split, args.cases)}
    if client is not None:
        experiments.require_guarded(client)
    b_runs = {(row["id"], row["repeat"]): row for row in baseline["runs"]}
    c_runs = {(row["id"], row["repeat"]): row for row in candidate["runs"]}
    if set(b_runs) != set(c_runs):
        raise ValueError("Answer reports do not have identical paired runs")
    repeats = max(Counter(key[0] for key in b_runs if key[0] in cases).values(), default=1)
    experiment = (experiments.execution(args, list(cases), repeats)
                  if client is None and not getattr(args, "reuse_only", False) else None)
    previous, previous_sha = prior_report(args)
    previous_rows = {(r["id"], r["repeat"]): r for r in previous.get("pairs", [])} if previous else {}
    report = {"schema_version": 1, "kind": "paired-answer-judgment", "judge_model": JUDGE_MODEL,
              "fixture_sha256": baseline["fixture_sha256"], "context_commit": baseline["context_commit"],
              "baseline_report_sha256": evidence.digest(args.baseline.read_bytes()),
              "candidate_report_sha256": evidence.digest(args.candidate.read_bytes()),
              "criteria": list(rubric()), "seed": args.seed, "pairs": [],
              "generation_summaries": {"baseline": baseline["summary"], "candidate": candidate["summary"]},
              "evidence_id_version": 2,
              "method": "One blinded paired judge call per case/repeat; public answer IDs mechanically matched to original model excerpts using saved source identities. Unreferenced candidates retain independent U labels. No system prompts or additional body text. Group summaries keep paraphrases together; no aggregate pass score."}
    semaphore = asyncio.Semaphore(args.concurrency)
    stopped = asyncio.Event()

    async def one(key, provider):
        case_id, repeat = key
        if case_id not in cases:
            return
        case, order = cases[case_id], blind_order(case_id, repeat, args.seed)
        row = {"id": case_id, "group": case["group"], "repeat": repeat, "order": order}
        values = {"baseline": b_runs[key], "candidate": c_runs[key]}
        if any("answer" not in value for value in values.values()):
            row["error"] = "generation_failed"
        else:
            try:
                views = {side: judge_evidence(values[side], (baseline if side == "baseline" else candidate)["inputs"][case_id])
                         for side in values}
            except (ValueError, KeyError, TypeError) as exc:
                row["error"] = "evidence_mapping_" + type(exc).__name__
                row["mapping_error"] = str(exc)
                report["pairs"].append(row)
                report["pairs"].sort(key=lambda item: (item["id"], item["repeat"]))
                report["summary"] = judge_summary(report["pairs"], report["criteria"])
                evidence.private_write(args.output, report)
                return
            shown = {label: views[side][0] for label, side in order.items()}
            row["source_mapping"] = {side: views[side][1] for side in values}
            content = {"question": case["question"], "context": case.get("context", ""), "history": case.get("history", []),
                       "intent": case["intent"], "expect": case["expect"], "good": case.get("good", ""),
                       "independent_gold": gold_evidence(fixture, case, ROOT / "data/context"), "answers": shown}
            # judge_evidence only emits the original excerpts with corrected labels;
            # full messages and mapping audit remain private and out of the blind input.
            payload = {"model": JUDGE_MODEL, "reasoning_effort": "low", "response_format": {"type": "json_object"},
                       "max_tokens": 4000,
                       "messages": [{"role": "system", "content": judge_prompt()},
                                    {"role": "user", "content": json.dumps(content, ensure_ascii=False)}]}
            async with semaphore:
                try:
                    row["request_sha256"] = experiments.digest(payload)
                    cached = previous_rows.get(key)
                    if cached and cached.get("order") == order and cached.get("request_sha256") == row["request_sha256"] and not getattr(args, "fresh", False) and (
                            "judgment" in cached or not getattr(args, "retry_errors", False)):
                        if "judgment" in cached:
                            saved = cached["judgment"]
                            validate_judgment({**{label: saved[side] for label, side in order.items()},
                                "preference": next((label for label, side in order.items() if side == saved["preference"]), "tie"),
                                "reason": saved["reason"]})
                        row = {**copy.deepcopy(cached), "reused": True, "reused_from_sha256": previous_sha}
                    elif cached and not getattr(args, "fresh", False) and cached.get("request_sha256") != row["request_sha256"]:
                        raise experiments.ExperimentStop("Judge input changed; calibrate a small sample before explicitly requesting --fresh")
                    elif stopped.is_set():
                        row.update(error="experiment_stopped", skipped=True)
                    else:
                        response = await provider.post(URL, json=payload, headers={"Authorization": "Bearer " + token}, timeout=180, follow_redirects=False)
                        response.raise_for_status()
                        value = validate_judgment(json.loads(response.json()["choices"][0]["message"]["content"]))
                        row["judgment"] = {side: value[label] for label, side in order.items()}
                        row["judgment"].update(preference=order.get(value["preference"], "tie"), reason=value["reason"])
                except experiments.ExperimentStop as exc:
                    row.update(error="experiment_stopped", skipped=True)
                    report["stopped"] = str(exc)
                    stopped.set()
                    if experiment is not None:
                        experiment.stop(str(exc))
                except Exception as exc:
                    row["error"] = "judge_" + type(exc).__name__
        report["pairs"].append(row)
        report["pairs"].sort(key=lambda item: (item["id"], item["repeat"]))
        report["summary"] = judge_summary(report["pairs"], report["criteria"])
        evidence.private_write(args.output, report)

    async def run(provider):
        await asyncio.gather(*(one(key, provider) for key in sorted(b_runs)))

    if client is None:
        async with experiments.paid_client(experiment, token, "judge") as provider:
            await run(provider)
    else:
        await run(client)
    report["summary"] = judge_summary(report["pairs"], report["criteria"])
    if experiment is not None:
        report["experiment"] = experiment.receipt()
    evidence.private_write(args.output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    generate_parser = commands.add_parser("generate")
    evidence.add_inputs(generate_parser)
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument("--model")
    generate_parser.add_argument("--repeats", type=int, default=1)
    generate_parser.add_argument("--concurrency", type=int, default=2)
    judge_parser = commands.add_parser("judge", aliases=["compare"])
    judge_parser.add_argument("--fixture", type=Path, default=evidence.DEFAULT_FIXTURE)
    judge_parser.add_argument("--baseline", type=Path, required=True)
    judge_parser.add_argument("--candidate", type=Path, required=True)
    judge_parser.add_argument("--output", type=Path, required=True)
    judge_parser.add_argument("--seed", default="ask-evidence-v1")
    judge_parser.add_argument("--split", choices=("dev", "holdout"))
    judge_parser.add_argument("--cases", nargs="+")
    judge_parser.add_argument("--concurrency", type=int, default=2)
    for command in (generate_parser, judge_parser):
        experiments.add_paid_args(command, reuse=True)
    args = parser.parse_args(argv)
    try:
        fixture = evidence.load_fixture(args.fixture)
        case_ids = [c["id"] for c in evidence.select_cases(fixture, args.split, args.cases)]
        repeats = args.repeats if args.mode == "generate" else max(Counter(
            r["id"] for r in json.loads(args.baseline.read_text())["runs"] if r["id"] in case_ids).values(), default=1)
        if experiments.preview(args, case_ids, repeats):
            return
        token = os.getenv("AI_BUILDER_TOKEN", "") if args.execute else ""
        report = asyncio.run(generate(args, token) if args.mode == "generate" else judge(args, token))
        print(json.dumps(report["summary"], ensure_ascii=False))
        if report.get("stopped"):
            raise SystemExit("Experiment stopped; completed work was saved: " + report["stopped"])
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
