from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server.answers import (DEFAULT_MODEL, ModelAnswer, answer_fields, assemble_answer, misattributed, numbers_as_names, short_title,
                            unlabel, validate_answer)
from server.app import create_app
from server.retrieval import ContextIndex

PROJECT = Path(__file__).resolve().parents[1]


def test_required_lock_fails_before_public_code_import(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/search.py").write_text("raise RuntimeError('must not execute')")
    with pytest.raises(ValueError, match="lock is required"):
        ContextIndex(tmp_path, require_lock=True).load()


def test_changed_release_fails_before_public_code_import(tmp_path):
    root = tmp_path / "context"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/search.py").write_text("raise RuntimeError('must not execute')")
    (root / "release-manifest.json").write_text("{}")
    (tmp_path / "context-lock.json").write_text(json.dumps({"release_manifest_sha256": "outdated"}))
    with pytest.raises(ValueError, match="lock does not match"):
        ContextIndex(root).load()


def source_file(root, folder, name, metadata, body):
    target = root / folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    front = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    target.write_text("---\n" + front + "\n---\n\n" + body)


@pytest.fixture
def context_pack(tmp_path):
    (tmp_path / "scripts").mkdir()
    public_root = Path(os.getenv("ASK_TEST_CONTEXT_ROOT", str(PROJECT / "data" / "context")))
    shutil.copy(public_root / "scripts" / "search.py", tmp_path / "scripts" / "search.py")
    (tmp_path / "release-manifest.json").write_text(json.dumps({"snapshot_at": "2026-09-30", "counts": {"video_transcripts": 1}}))
    shared = {"published_at": "2025-04-01", "content_status": "current", "author": "Yuzheng Sun",
              "evidence_role": "primary-authored", "yuzheng_stance_weight": "direct",
              "attribution_note": "测试用公开材料", "source_context": "Synthetic author-owned test fixture"}
    source_file(tmp_path, "corpus/community-posts", "career.md", {
        **shared, "id": "career-public", "title": "职业选择要看能力与作品", "source_type": "community-post",
        "source_url": "https://example.org/career", "source_family": "career-family",
    }, "职业选择可以从已有能力与具体作品开始。作品能够提供实际价值的证据，而职位名称不能替代能力。\n\n先比较目标岗位需要的能力与已有作品，找到一个可以用实践验证的差距。")
    timed = "\n\n".join(f"[00:{minute:02}:00](https://www.youtube.com/watch?v=testvideo&t={minute * 60}s) " + ("职业选择要结合能力与作品验证。" if minute == 15 else "介绍一个无关的背景片段。") for minute in range(22))
    source_file(tmp_path, "corpus/videos", "video.md", {
        **shared, "id": "video-public", "title": "如何积累能力", "source_type": "video-transcript",
        "source_url": "https://www.youtube.com/watch?v=testvideo", "source_family": "video-family",
        "evidence_role": "primary-speech",
    }, timed)
    source_file(tmp_path, "corpus/english-translations", "translation.md", {
        **shared, "id": "translated-public", "title": "职业选择的 AI 翻译", "source_type": "video-translation",
        "source_url": "https://example.org/translation", "source_family": "video-family",
        "evidence_role": "translated-reading-aid", "original_author": "Yuzheng Sun",
    }, "职业选择要结合能力与作品验证。")
    source_file(tmp_path, "corpus/english-community", "guest.md", {
        **shared, "id": "other-author", "title": "职业选择：第三方观点", "source_type": "english-community",
        "source_url": "https://example.org/other-author", "source_family": "other-family", "author": "Guest Author",
        "original_author": "Guest Author", "yuzheng_stance_weight": "not-evidence",
        "attribution_note": "原作者观点，不代表发布者立场",
    }, "职业选择需要评估生活约束，能力与作品只是其中一部分。")
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog/videos.jsonl").write_text(json.dumps({
        "id": "guest-metadata", "title": "粒子物理嘉宾访谈", "url": "https://example.org/guest-video",
        "guest_names": ["Guest Physicist"], "evidence_role": "metadata-only", "source_family": "guest-video-family",
        "attribution_note": "仅标题，不含访谈正文", "author": "Guest Physicist",
    }, ensure_ascii=False) + "\n")
    return tmp_path


@pytest.fixture
def index(context_pack):
    index = ContextIndex(context_pack)
    index.load()
    return index


def test_source_family_dedup_and_guest_author(index):
    passages = index.retrieve("职业选择如何积累能力与作品")
    families = [passage.evidence["source_family"] for passage in passages]
    assert len(families) == len(set(families))
    guest = next(passage for passage in passages if passage.source["url"] == "https://example.org/other-author")
    assert guest.source["author"] == "Guest Author"
    assert guest.evidence["yuzheng_stance_weight"] == "not-evidence"


def test_video_anchor_matches_retrieved_passage(index):
    # Exclude the alternate-language rendering to inspect the original segment.
    index.documents = [doc for doc in index.documents if doc.source_type != "video-translation"]
    index._texts = [doc.text.lower() for doc in index.documents]
    index._titles = [(doc.title + doc.section).lower() for doc in index.documents]
    video = next(passage for passage in index.retrieve("职业选择") if passage.source["source_type"] == "video-transcript")
    assert video.source["timecode"] == "00:15:00"
    assert "t=900s" in video.source["url"]
    assert "职业选择要结合能力" in video.source["excerpt"]


def test_long_paragraph_keeps_matching_text(index):
    doc = index.documents[0]
    doc.text = "普通背景。" * 900 + "职业选择匹配能力与作品。" + "普通结尾。" * 600
    excerpt, _, _ = index._excerpt(doc, ["职业选择"], {"职业选择": 5})
    assert "职业选择匹配能力与作品" in excerpt
    assert len(excerpt) <= 2101


def answer_for(source_id="S1", body="可以先比较目标岗位需要的能力与已有作品。"):
    return ModelAnswer(status="answered", summary="用具体作品验证能力。", sections=[{
        "heading": "先看能力证据", "body": body, "source_ids": [source_id], "kind": "application",
    }], followups=[], clarifying_questions=[], limitations="这是对公开材料的应用。")


def test_answer_owns_no_metadata_and_rejects_unknown_citations(index):
    passages = index.retrieve("职业选择")
    answer = answer_for()
    validate_answer(answer, passages)
    result = assemble_answer(answer, passages)
    assert result["sources"] == [passages[0].source]
    with pytest.raises(ValueError):
        validate_answer(answer_for("S999"), passages)
    with pytest.raises(ValueError):
        validate_answer(answer_for(body="参见 [S999] 的建议。"), passages)
    with pytest.raises(ValueError):
        validate_answer(answer_for(body="打开 https://malicious.example/ 看原话。"), passages)
    with pytest.raises(ValueError):
        validate_answer(answer_for(body="他说“这里是模型编造的一整句没有来源支持的直接原话”。"), passages)


def test_discovery_only_cannot_support_answer(index):
    passages = index.retrieve("粒子物理")
    assert passages and all(passage.discovery for passage in passages)
    with pytest.raises(ValueError):
        validate_answer(answer_for(), passages)


def test_unsupported_model_answer_does_not_recommend_weak_matches(index):
    passages = index.retrieve("职业选择")
    answer = ModelAnswer(status="unsupported", summary="资料不足。", sections=[], followups=[], clarifying_questions=[], limitations="")
    assert assemble_answer(answer, passages)["sources"] == []


def test_ordinary_concept_quotes_and_source_titles_are_allowed(index):
    passages = index.retrieve("职业选择")
    answer = answer_for(body="你提出的“我做出了几个项目，怎样知道自己真的学会了”可以从迁移能力去检验。")
    validate_answer(answer, passages)
    with pytest.raises(ValueError):
        validate_answer(answer_for(body="作者原话是“只要你做出了几个项目就一定证明自己真正掌握了能力”。"), passages)


def test_provider_has_real_wall_clock_budget_without_retry(index, monkeypatch):
    import asyncio
    import time
    from server.answers import ProviderFailure, generate_answer
    calls = []
    monkeypatch.setattr("server.answers.ANSWER_BUDGET_SECONDS", .03)
    async def slow_provider(request):
        calls.append(request)
        # MockTransport does not honor httpx's per-read timeout. This checks
        # the outer wall-clock guard rather than mirroring httpx settings.
        await asyncio.sleep(.3)
        return httpx.Response(200, json={})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow_provider)) as client:
            start = time.monotonic()
            with pytest.raises(ProviderFailure) as error:
                await generate_answer(client, "synthetic-token", "gpt-5", {"question": "职业选择"}, index.retrieve("职业选择"))
            assert error.value.code == "provider_timeout"
            assert time.monotonic() - start < .2
    asyncio.run(run())
    assert len(calls) == 1


def test_source_reasons_use_known_ids_and_server_owned_metadata(index):
    passages = index.retrieve("职业选择")
    answer = answer_for()
    answer = ModelAnswer.model_validate({**answer.model_dump(), "source_reasons": [{"source_id": "S1", "reason": "可对照作品如何证明能力，检验你的项目展示。"}]})
    validate_answer(answer, passages)
    result = assemble_answer(answer, passages)
    assert result["sources"][0]["reason"] == answer.source_reasons[0].reason
    assert result["sources"][0]["url"] == passages[0].source["url"]
    assert "source_reasons" not in result
    answer = ModelAnswer.model_validate({**answer.model_dump(), "source_reasons": [{"source_id": "S99", "reason": "无法核对的来源"}]})
    with pytest.raises(ValueError):
        validate_answer(answer, passages)


def events(response):
    return [(part.splitlines()[0].removeprefix("event: "), json.loads(next(line[6:] for line in part.splitlines() if line.startswith("data: ")))) for part in response.text.strip().split("\n\n") if any(line.startswith("data: ") for line in part.splitlines())]


def test_only_existing_hashed_assets_are_cached(context_pack, tmp_path, monkeypatch):
    import server.app as app_module

    site = tmp_path / "site"
    (site / "dist/assets").mkdir(parents=True)
    (site / "dist/index.html").write_text("<!doctype html><title>问问立正</title>")
    (site / "dist/assets/index-abc123.js").write_text("console.log(1)")
    with TestClient(create_app(context_pack)) as client:
        monkeypatch.setattr(app_module, "ROOT", site)
        asset = client.get("/assets/index-abc123.js")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        missing = client.get("/assets/index-old.js")
        assert missing.status_code == 404
        assert "immutable" not in missing.headers.get("cache-control", "")
        page = client.get("/")
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        deep = client.get("/some/where")
        assert "问问立正" in deep.text and deep.headers["cache-control"] == "no-store"


def test_no_token_returns_honest_search_results(context_pack, monkeypatch):
    monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)
    with TestClient(create_app(context_pack)) as client:
        meta = client.get("/api/meta").json()
        assert meta["mode"] == "search-only" and not meta["model_ready"]
        response = client.post("/api/ask", json={"question": "职业选择怎么做", "intent": "apply"})
        assert response.headers["cache-control"] == "no-store, no-transform"
        assert response.headers["x-accel-buffering"] == "no"
        result = events(response)[-1][1]
        assert result["status"] == "sources-only" and result["sources"]
        assert not result["sections"]
        search = client.get("/api/search", params={"q": "职业选择"})
        assert search.json()["sources"]


@pytest.mark.parametrize("configured_model", [None, "gpt-5"])
def test_consent_model_mismatch_stops_before_admission_resources(context_pack, monkeypatch, configured_model):
    from server.quota import MemoryQuotaStore
    from test_quota import SECRET, post

    monkeypatch.setenv("ASK_QUOTA_ENABLED", "true")
    monkeypatch.setenv("ASK_ADMISSION_SECRET", SECRET)
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    if configured_model is None:
        monkeypatch.delenv("AI_MODEL", raising=False)
    else:
        monkeypatch.setenv("AI_MODEL", configured_model)
    current_model = configured_model or DEFAULT_MODEL

    def unexpected(*args, **kwargs):
        pytest.fail("A consent mismatch must not consume resources or transmit input")

    app = create_app(context_pack, httpx.MockTransport(unexpected), quota_store=MemoryQuotaStore(),
                     query_record_transport=httpx.MockTransport(unexpected))
    with TestClient(app) as client:
        for target, name in [(app.state.limiter, "allow"), (app.state.slots, "acquire"),
                             (app.state.quota, "reserve"), (app.state.semantic, "candidates"),
                             (app.state.index, "retrieve"), (app.state.query_records, "enqueue"),
                             (app.state.ops_records, "prepare"), (app.state.ops_records, "start")]:
            monkeypatch.setattr(target, name, unexpected)
        response = post(client, {"question": "职业选择怎么做", "query_log_notice": "v1",
                                 "ai_consent_model": "previous-model"})
        assert response.status_code == 409
        assert response.json() == {"code": "ai_consent_changed", "model": current_model}
        assert response.headers["x-ask-error-code"] == "ai_consent_changed"
        assert response.headers["cache-control"] == "no-store"
        assert app.state.slots._value == 3 and not app.state.query_records.tasks


@pytest.mark.parametrize("with_consent_model", [True, False])
def test_matching_or_legacy_consent_binds_model_without_forwarding_field(context_pack, monkeypatch, with_consent_model):
    from server.quota import MemoryQuotaStore
    from test_quota import SECRET, post

    monkeypatch.setenv("ASK_QUOTA_ENABLED", "true")
    monkeypatch.setenv("ASK_ADMISSION_SECRET", SECRET)
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "true")
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    monkeypatch.setenv("AI_MODEL", "gpt-5")
    monkeypatch.setenv("ASK_OPS_ENABLED", "false")
    generated, recorded = [], []

    def provider(request):
        body = json.loads(request.content)
        generated.append(body)
        prompt = json.loads(body["messages"][1]["content"])
        assert "ai_consent_model" not in prompt and "query_log_notice" not in prompt
        return httpx.Response(200, json={"choices": [{"message": {"content": answer_for().model_dump_json()}, "finish_reason": "stop"}]})

    app = create_app(context_pack, httpx.MockTransport(provider), quota_store=MemoryQuotaStore())
    with TestClient(app) as client:
        reserve = app.state.quota.reserve

        async def reserve_then_change_model(principal):
            # A deployment/config change after the guard cannot redirect this request.
            monkeypatch.setenv("AI_MODEL", "changed-model")
            return await reserve(principal)

        monkeypatch.setattr(app.state.quota, "reserve", reserve_then_change_model)
        monkeypatch.setattr(app.state.query_records, "enqueue", lambda **record: recorded.append(record))
        payload = {"question": "职业选择怎么做", "query_log_notice": "v1"}
        if with_consent_model:
            payload["ai_consent_model"] = "gpt-5"
        response = post(client, payload)
        assert response.status_code == 200 and events(response)[-1][1]["status"] == "answered"
        assert len(generated) == len(recorded) == 1
        assert generated[0]["model"] == recorded[0]["model"] == "gpt-5"
        assert "ai_consent_model" not in recorded[0]


def test_consent_model_field_is_bounded(context_pack):
    with TestClient(create_app(context_pack)) as client:
        for model in ["", "x" * 129]:
            response = client.post("/api/ask", json={"question": "职业选择", "ai_consent_model": model})
            assert response.status_code == 422 and response.json()["code"] == "invalid_input"


def test_meta_names_the_release_it_answers_from(context_pack, monkeypatch):
    monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)
    with TestClient(create_app(context_pack)) as client:
        meta = client.get("/api/meta").json()
    assert meta["context_release"] == hashlib.sha256((context_pack / "release-manifest.json").read_bytes()).hexdigest()
    assert meta["context_commit"] == ""


def test_provider_answer_is_source_validated(context_pack, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    monkeypatch.setenv("AI_MODEL", "grok-4.5")
    def provider(request):
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["model"] == "grok-4.5"
        assert body["reasoning_effort"] == "medium"
        assert "先确定 status，再写 sections" in body["messages"][0]["content"]
        prompt = json.loads(body["messages"][1]["content"])
        assert prompt["sources"][0]["attribution_note"]
        assert prompt["sources"][0]["excerpt"]
        assert "evidence_role" in prompt["sources"][0]
        assert "discovery_only" in prompt["sources"][0]
        assert not {"url", "public_copy_url", "source_path", "docindex", "reason"}.intersection(prompt["sources"][0])
        return httpx.Response(200, json={"choices": [{"message": {"content": answer_for().model_dump_json()}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        response = client.post("/api/ask", json={"question": "职业选择怎么做"})
        items = events(response)
        assert [event for event, _ in items] == ["progress", "sources", "progress", "sources", "approach", "progress", "progress", "result"]
        assert items[4][1]["sources"] and "尚不是完整回答" in items[4][1]["note"]
        assert items[1][1]["provisional"] and items[1][1]["sources"][0]["excerpt"]
        assert items[-2][1]["stage"] == "checking"
        assert items[-1][1]["status"] == "answered"
        assert items[-1][1]["sources"][0]["url"].startswith("https://")


@pytest.mark.parametrize("model,effort", [("deepseek-v4-pro", "high"), ("deepseek-v4-flash", "low")])
def test_deepseek_json_object_retains_schema_and_source_validation(context_pack, monkeypatch, model, effort):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    monkeypatch.setenv("AI_MODEL", model)
    attempts = []
    def provider(request):
        body = json.loads(request.content)
        attempts.append(body)
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == effort
        assert '"additionalProperties": false' in body["messages"][0]["content"]
        # A provider accepting JSON mode does not bypass citation validation.
        answer = answer_for("S999" if len(attempts) == 1 else "S1")
        return httpx.Response(200, json={"choices": [{"message": {"content": answer.model_dump_json()}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        assert client.get("/api/meta").json()["reasoning_effort"] == effort
        assert events(client.post("/api/ask", json={"question": "职业选择怎么做"}))[-1][1]["status"] == "answered"
        assert len(attempts) == 2


def test_gpt5_option_uses_low_reasoning_without_overriding_client_input(context_pack, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    monkeypatch.setenv("AI_MODEL", "gpt-5")
    def provider(request):
        body = json.loads(request.content)
        assert body["model"] == "gpt-5" and body["reasoning_effort"] == "low"
        return httpx.Response(200, json={"choices": [{"message": {"content": answer_for().model_dump_json()}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        assert client.get("/api/meta").json()["reasoning_effort"] == "low"
        assert events(client.post("/api/ask", json={"question": "职业选择怎么做"}))[-1][1]["status"] == "answered"
        assert client.post("/api/ask", json={"question": "职业选择", "model": "other-model"}).status_code == 422


def test_invalid_source_gets_one_targeted_repair(context_pack, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    attempts = []
    def provider(request):
        body = json.loads(request.content)
        attempts.append(body)
        if len(attempts) == 1:
            answer = answer_for("S999")
        else:
            assert len(body["messages"]) == 4
            assert "有效 source_ids" in body["messages"][-1]["content"]
            answer = answer_for()
        return httpx.Response(200, json={"choices": [{"message": {"content": answer.model_dump_json()}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        items = events(client.post("/api/ask", json={"question": "职业选择怎么做"}))
        assert any(value.get("stage") == "repairing" for event, value in items if event == "progress")
        assert not any(event == "result" and "S999" in json.dumps(value) for event, value in items)
        result = items[-1][1]
        assert result["status"] == "answered"
        assert len(attempts) == 2


def test_naming_yuzheng_for_another_speaker_gets_one_rewrite(context_pack, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    attempts = []
    def provider(request):
        body = json.loads(request.content)
        evidence = json.loads(body["messages"][1]["content"])["sources"]
        guest = next(item["id"] for item in evidence if item.get("author") == "Guest Author")
        attempts.append(body)
        if len(attempts) == 1:
            answer = answer_for(guest, f"立正提到，职业选择要评估生活约束 [{guest}]。")
        else:
            assert "不是立正本人的材料" in body["messages"][-1]["content"]
            answer = answer_for(guest, f"一位作者提到，职业选择要评估生活约束 [{guest}]。")
        return httpx.Response(200, json={"choices": [{"message": {"content": answer.model_dump_json()}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        result = events(client.post("/api/ask", json={"question": "职业选择要评估生活约束吗"}))[-1][1]
        assert result["status"] == "answered" and "立正" not in result["sections"][0]["body"]
        assert len(attempts) == 2


def test_a_second_naming_slip_keeps_the_answer(index):
    passages = index.retrieve("职业选择要评估生活约束吗")
    guest = next(passage.source["id"] for passage in passages if passage.source["author"] == "Guest Author")
    answer = answer_for(guest, f"立正提到，职业选择要评估生活约束 [{guest}]。立正的文章没有讨论这一点。")
    assert misattributed(answer, passages) == [f"立正提到，职业选择要评估生活约束 [{guest}]。"]


def test_citations_stay_with_their_sentence(index):
    passages = index.retrieve("职业选择如何积累能力与作品")
    sid = passages[0].source["id"]
    answer = answer_for(sid, f"先比较能力与作品。[{sid}] 再找一个差距。[{sid}][{sid}]")
    assert assemble_answer(answer, passages)["sections"][0]["body"] == f"先比较能力与作品[{sid}]。再找一个差距[{sid}][{sid}]。"
    # At a paragraph's end the stop stays in that paragraph.
    answer = answer_for(sid, f"先比较能力与作品。[{sid}]\n\n再找一个差距。")
    assert assemble_answer(answer, passages)["sections"][0]["body"] == f"先比较能力与作品[{sid}]。\n\n再找一个差距。"


def test_source_numbers_touching_chinese_are_checked(index):
    passages = index.retrieve("职业选择如何积累能力与作品")
    # \b sees no edge between 与 and S, so these once passed: an unknown number, and one the section does not cite.
    with pytest.raises(ValueError):
        validate_answer(answer_for(body="这一点可与S9相互印证。"), passages)
    with pytest.raises(ValueError):
        validate_answer(answer_for(body="这一点和S2说的一致。"), passages)


def test_numbers_written_as_names_are_found(index):
    passages = index.retrieve("职业选择如何积累能力与作品")
    answer = ModelAnswer.model_validate({**answer_for(body="先比较能力与作品[S1]。S1 说得更直接——作品是证据。").model_dump(),
                                         "followups": ["S2 讲的方法怎么用？"], "source_reasons": [{"source_id": "S1", "reason": "可与S2相互印证。"}]})
    validate_answer(answer, passages)
    assert numbers_as_names(answer_fields(answer)) == ["S1 说得更直接——作品是证据。", "S2 讲的方法怎么用？", "可与S2相互印证。"]
    # A mark is how readers see a source, except where marks do not show, such as a heading.
    assert numbers_as_names([("先比较能力与作品[S1]。", True)]) == []
    assert numbers_as_names([("先看能力证据[S1]", False)]) == ["先看能力证据[S1]"]


def test_a_number_written_as_a_name_gets_one_rewrite(context_pack, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    attempts = []
    def provider(request):
        body = json.loads(request.content)
        attempts.append(body)
        if len(attempts) == 1:
            answer = ModelAnswer.model_validate({**answer_for(body="S1 说得更直接：作品是能力的证据。").model_dump(),
                                                 "source_reasons": [{"source_id": "S1", "reason": "可与S2相互印证。"}]})
        else:
            assert "当成名字" in body["messages"][-1]["content"] and "「S1 说得更直接：作品是能力的证据。」" in body["messages"][-1]["content"]
            answer = answer_for(body="作品是能力的证据[S1]。")
        return httpx.Response(200, json={"choices": [{"message": {"content": answer.model_dump_json()}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        result = events(client.post("/api/ask", json={"question": "职业选择怎么做"}))[-1][1]
    assert result["status"] == "answered" and result["sections"][0]["body"] == "作品是能力的证据[S1]。"
    assert len(attempts) == 2


@pytest.mark.parametrize("failure", ["invalid-json", "unknown-source", "timeout", "busy"])
def test_a_failed_rewrite_keeps_the_first_answer(context_pack, monkeypatch, failure):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    attempts = []
    def provider(request):
        attempts.append(True)
        if len(attempts) == 1:
            content = answer_for(body="S1 说得更直接：作品是能力的证据。").model_dump_json()
        elif failure == "timeout":
            raise httpx.ReadTimeout("Synthetic timeout")
        elif failure == "busy":
            return httpx.Response(429)
        else:
            content = "{" if failure == "invalid-json" else answer_for("S900").model_dump_json()
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        result = events(client.post("/api/ask", json={"question": "职业选择怎么做"}))[-1][1]
    title = next(source["title"] for source in result["sources"] if source["id"] == "S1")
    assert result["status"] == "answered" and result["sections"][0]["body"] == f"《{title}》[S1]说得更直接：作品是能力的证据。"
    assert len(attempts) == 2


def test_a_second_slip_reads_as_titles(index):
    # A rewrite that slips again, or no time for one: each number becomes its source's title, marked where marks show.
    passages = index.retrieve("职业选择如何积累能力与作品")
    answer = ModelAnswer.model_validate({
        "status": "answered", "summary": "作品能证明能力[S1]。", "limitations": "", "clarifying_questions": [],
        "sections": [{"heading": "先看能力证据[S1]", "body": "S1 说得更直接：作品是能力的证据。这和 S2 的看法一致（S1、S2）。",
                      "source_ids": ["S1", "S2"], "kind": "synthesis"}],
        "followups": ["S2 讲的方法怎么用？"], "source_reasons": [{"source_id": "S2", "reason": "可与S1相互印证。"}]})
    validate_answer(answer, passages)
    result = assemble_answer(answer, passages)
    section = result["sections"][0]
    assert section["heading"] == "先看能力证据"
    assert section["body"] == "《职业选择要看能力与作品》[S1]说得更直接：作品是能力的证据。这和《如何积累能力》[S2]的看法一致[S1][S2]。"
    assert result["followups"] == ["《如何积累能力》讲的方法怎么用？"]
    assert result["sources"][1]["reason"] == "可与《职业选择要看能力与作品》相互印证。"
    # A title is longer than its number; past the field's limit, only the mark stays.
    long = answer.model_copy(update={"summary": "能" * 340 + "，S1 也这样看。"})
    assert assemble_answer(long, passages)["summary"] == "能" * 340 + "，[S1]也这样看。"


def test_sources_are_numbered_in_reading_order(index):
    passages = index.retrieve("职业选择如何积累能力与作品")
    answer = ModelAnswer.model_validate({**answer_for("S3", "作品是证据[S3]。再找一个差距[S1]。").model_dump(), "summary": "先看作品[S3]。"})
    answer.sections[0].source_ids = ["S3", "S1"]
    validate_answer(answer, passages)
    result = assemble_answer(answer, passages)
    assert result["summary"] == "先看作品[S1]。"
    assert result["sections"][0]["body"] == "作品是证据[S1]。再找一个差距[S2]。" and result["sections"][0]["source_ids"] == ["S1", "S2"]
    assert [(source["id"], source["url"]) for source in result["sources"]] == [("S1", passages[2].source["url"]), ("S2", passages[0].source["url"])]
    # A partial answer keeps the model's numbers: pages match its sources to the candidates by number.
    partial = assemble_answer(answer, passages, final=False)
    assert [source["id"] for source in partial["sources"]] == ["S1", "S3"] and partial["sections"][0]["source_ids"] == ["S3", "S1"]


def test_titles_sit_in_the_sentence_without_stray_spaces():
    titles = {"S1": "《甲》", "S2": "《乙》"}
    # Bracketed numbers become marks first; the named number after them still reads its own neighbours.
    assert unlabel("（S1、S2）都这样看。S2 更直接。", titles, {"S1", "S2"}) == "[S1][S2]都这样看。《乙》[S2]更直接。"
    assert unlabel("见（S1、S2）。可与 S9 对照。", titles, None) == "见（《甲》、《乙》）。可与对照。"
    assert unlabel("As S1 shows", titles, None) == "As 《甲》 shows"


def test_a_mark_without_its_letter_gets_it_back():
    titles = {"S1": "《甲》", "S3": "《丙》"}
    # Asked to keep numbers out of sentences, the model once wrote [3] for [S3]; pages would show "[3]".
    assert unlabel("要关注定性的信息 [3]。可被证伪 [1]。", titles, {"S1", "S3"}) == "要关注定性的信息 [S3]。可被证伪 [S1]。"
    assert unlabel("先看证据[1]", titles, None) == "先看证据"
    assert unlabel("可与[1]相互印证。", titles, None) == "可与《甲》相互印证。"
    assert unlabel("数组从 [0] 开始。", titles, {"S1"}) == "数组从 [0] 开始。"


@pytest.mark.parametrize("title, short", [
    ("战术勤劳与战略懒惰：大厂为什么越忙，产品质量越差？", "《战术勤劳与战略懒惰》"),
    ("立正本人的话 · 打工人如何获得财富自由？｜什么才是真正的财富和真正的自由？（中文字幕）｜Multiple-Fire系列", "《打工人如何获得财富自由？》"),
    ("《Growth Data Analytics Playbook》中文版 · 第3章　用growth accounting打地基", "《Growth Data Analytics Playbook》"),
    ("【限时公开】个体创业后，才发现打工最让我难受的，是“沟通税”", "《个体创业后，才发现打工最让我难受的，是“沟通税”》"),
    ("一二三四五六七八九十，一二三四五六七八九十一二三四五六七八", "《一二三四五六七八九十》"),
    ("How to be strategic?", "《How to be strategic?》"),
])
def test_a_title_short_enough_for_a_sentence(title, short):
    assert short_title(title) == short


@pytest.mark.parametrize("failure", ["invalid-json", "unknown-source", "timeout", "busy", "bad-auth"])
def test_provider_failure_preserves_retrieved_sources(context_pack, monkeypatch, failure):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    attempts = []
    def provider(request):
        attempts.append(True)
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic timeout")
        if failure in {"busy", "bad-auth"}:
            return httpx.Response(429 if failure == "busy" else 401)
        content = "{" if failure == "invalid-json" else answer_for("S900").model_dump_json()
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        result = events(client.post("/api/ask", json={"question": "职业选择怎么做"}))[-1][1]
        assert result["status"] == "sources-only" and result["sources"]
        assert result["limitations"]
        assert result["retryable"] and result["failure_code"]
        assert len(attempts) == (2 if failure in {"invalid-json", "unknown-source"} else 1)


def test_unsupported_question_does_not_call_provider(context_pack, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    def provider(request):
        pytest.fail("No evidence must not trigger an invented answer")
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        result = events(client.post("/api/ask", json={"question": "zyxqvork"}))[-1][1]
        assert result["status"] == "unsupported"
        result = events(client.post("/api/ask", json={"question": "粒子物理"}))[-1][1]
        assert result["status"] == "unsupported" and result["sources"]


def test_unnamed_guest_requires_identity_before_provider_call(context_pack, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    def provider(request):
        pytest.fail("Another speaker's excerpt must not answer an unnamed guest claim")
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        result = events(client.post("/api/ask", json={"question": "某位嘉宾在访谈里对未来收益有什么保证？"}))[-1][1]
        assert result["status"] == "clarify" and not result["sections"] and not result["sources"]
        assert result["clarifying_questions"]


def test_guest_identity_guard_allows_supplied_context():
    from server.answers import attribution_clarification
    assert attribution_clarification("某位嘉宾说了什么？", context="指九月二十九日的周洁访谈") is None
    assert attribution_clarification("某位嘉宾的访谈怎么听才好？") is None


def test_input_limits_errors_and_rate_limit(context_pack, monkeypatch):
    monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)
    monkeypatch.setenv("ASK_RATE_PER_MINUTE", "2")
    with TestClient(create_app(context_pack)) as client:
        sentinel = "PRIVATE_INPUT_SENTINEL"
        response = client.post("/api/ask", json={"question": sentinel * 120})
        assert response.status_code == 422 and sentinel not in response.text
        assert client.post("/api/ask", json={"question": "  "}).status_code == 422
        assert client.post("/api/ask", json={"question": "职业选择", "history": [{"question": "q", "summary": "s"}] * 7}).status_code == 422
        assert client.post("/api/ask", content=b"x" * 80001).status_code == 413
        assert client.post("/api/ask", content=(b"x" * 30000 for _ in range(3))).status_code == 413
        assert client.post("/api/ask", json={"question": "职业选择"}).status_code == 200
        assert client.post("/api/ask", json={"question": "职业选择"}).status_code == 200
        assert client.post("/api/ask", json={"question": "职业选择"}).status_code == 429


def test_missing_context_has_recoverable_error(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_BUILDER_TOKEN", raising=False)
    with TestClient(create_app(tmp_path)) as client:
        health = client.get("/health")
        assert health.status_code == 503 and not health.json()["context_ready"]
        result = events(client.post("/api/ask", json={"question": "职业选择"}))[-1]
        assert result == ("error", {"message": "公开资料暂时无法读取，请稍后再试。", "code": "context_unavailable"})


def test_actual_sources_arrive_before_slow_semantic_lookup_and_cancel_frees_slot(context_pack, monkeypatch):
    import asyncio
    from server.app import AskRequest
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    cancelled = []
    async def slow_semantic(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr("server.app.SemanticIndex.candidates", slow_semantic)
    async def run():
        app = create_app(context_pack)
        async with app.router.lifespan_context(app):
            class Request:
                client = type("Client", (), {"host": "127.0.0.1"})()
                async def is_disconnected(self):
                    return False
            ask = next(route.endpoint for route in app.routes if route.path == "/api/ask")
            response = await ask(Request(), AskRequest(question="职业选择怎么做"))
            await anext(response.body_iterator)
            source_frame = await asyncio.wait_for(anext(response.body_iterator), .5)
            assert source_frame.startswith("event: sources\n")
            assert "职业选择要看能力与作品" in source_frame
            assert app.state.slots._value == 2
            await response.body_iterator.aclose()
            assert cancelled and app.state.slots._value == 3
    asyncio.run(run())


def test_disconnect_cancels_model_generation(context_pack, monkeypatch):
    import asyncio
    from server.app import AskRequest
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    cancelled = []
    started = None
    async def provider(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    async def run():
        nonlocal started
        started = asyncio.Event()
        app = create_app(context_pack, httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            class Request:
                client = type("Client", (), {"host": "127.0.0.1"})()
                async def is_disconnected(self):
                    return False
            ask = next(route.endpoint for route in app.routes if route.path == "/api/ask")
            response = await ask(Request(), AskRequest(question="职业选择怎么做"))
            for _ in range(6):
                await anext(response.body_iterator)
            pending = asyncio.create_task(anext(response.body_iterator))
            await asyncio.wait_for(started.wait(), .5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert cancelled and app.state.slots._value == 3
    asyncio.run(run())


def test_anyio_disconnect_cancellation_always_returns_generation_slot(context_pack, monkeypatch):
    import asyncio
    import importlib
    import anyio
    from starlette.requests import Request
    app_module = importlib.import_module("server.app")
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    monkeypatch.setenv("ASK_CONCURRENCY", "3")
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def blocking_generate(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        monkeypatch.setattr(app_module, "generate_answer", blocking_generate)
        app = app_module.create_app(context_pack)
        async with app.router.lifespan_context(app):
            scope = {"type": "http", "method": "POST", "path": "/api/ask", "headers": [],
                     "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8000),
                     "scheme": "http", "http_version": "1.1", "asgi": {"version": "3.0", "spec_version": "2.3"}}
            async def receive():
                await asyncio.sleep(0)
                return {"type": "http.request", "body": b"", "more_body": False}
            endpoint = next(route.endpoint for route in app.routes if route.path == "/api/ask")
            response = await endpoint(Request(scope, receive), app_module.AskRequest(question="职业选择如何验证能力？"))
            async def consume():
                async for _ in response.body_iterator:
                    pass
            async with anyio.create_task_group() as group:
                group.start_soon(consume)
                await asyncio.wait_for(started.wait(), 2)
                # Starlette uses a persistent cancellation scope on disconnect.
                group.cancel_scope.cancel()
            await asyncio.wait_for(cancelled.wait(), 1)
            assert app.state.slots._value == 3
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["semantic", "model"])
def test_idle_work_sends_heartbeat_and_still_finishes_once(context_pack, monkeypatch, phase):
    import asyncio
    import importlib
    from server.app import AskRequest
    app_module = importlib.import_module("server.app")
    monkeypatch.setenv("AI_BUILDER_TOKEN", "synthetic-placeholder-token")
    monkeypatch.setattr(app_module, "STREAM_HEARTBEAT_SECONDS", .01)
    calls = []

    async def run():
        release = asyncio.Event()

        async def semantic(*args):
            if phase == "semantic":
                await release.wait()
            return []

        async def provider(request):
            calls.append(True)
            if phase == "model":
                await release.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": answer_for().model_dump_json()}, "finish_reason": "stop"}]})

        monkeypatch.setattr(app_module.SemanticIndex, "candidates", semantic)
        app = create_app(context_pack, httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            class Request:
                client = type("Client", (), {"host": "127.0.0.1"})()
                async def is_disconnected(self):
                    return False
            ask = next(route.endpoint for route in app.routes if route.path == "/api/ask")
            response = await ask(Request(), AskRequest(question="职业选择如何验证能力？"))
            frames = []
            while True:
                frame = await asyncio.wait_for(anext(response.body_iterator), .5)
                frames.append(frame)
                if frame.startswith(": keep-alive "):
                    break
            assert any(frame.startswith("event: sources") for frame in frames)
            assert not any(frame.startswith("event: result") for frame in frames)
            assert len(frames[-1].encode()) >= 16384
            release.set()
            frames.extend([frame async for frame in response.body_iterator])
            parsed = events(httpx.Response(200, text="".join(frames)))
            assert parsed[-1][0] == "result" and parsed[-1][1]["status"] == "answered"
            assert sum(event == "result" for event, _ in parsed) == 1
            assert len(calls) == 1
            assert app.state.slots._value == 3
    asyncio.run(run())


def test_the_address_word_is_asked_for_but_never_part_of_the_answer(index):
    from server.answers import SYSTEM_PROMPT, strict_schema
    schema = strict_schema()
    assert "slug" in schema["required"] and schema["properties"]["slug"]["type"] == "string"
    assert "slug" in SYSTEM_PROMPT and "不写人名、公司名、地名" in SYSTEM_PROMPT
    passages = index.retrieve("怎样证明自己的能力")
    answer = ModelAnswer.model_validate({**answer_for().model_dump(), "slug": "career-choice"})
    assert "slug" not in assemble_answer(answer, passages)
    # An answer without the word still parses; the share address then falls back to a plain word.
    assert ModelAnswer.model_validate_json(answer_for().model_dump_json(exclude={"slug"})).slug == ""
