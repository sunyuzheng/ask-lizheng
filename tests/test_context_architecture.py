from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.context_architecture import CARDS_PATH, CardValidationError, load
from server.retrieval import ContextIndex, Passage


def make_pack(root, cards=None):
    original = root / "corpus/community-posts/original.md"
    contrast = root / "corpus/community-posts/contrast.md"
    original.parent.mkdir(parents=True)
    original.write_text("背景没有关联。\n\n撤掉帮助之后，换一道题才能检验迁移能力。", encoding="utf-8")
    contrast.write_text("学习项目与长期拥有产品的验收不同。", encoding="utf-8")
    card = {"id": "learning-transfer", "title": "交付与能力分别检验", "query_cues": ["真的学会", "撤掉帮助", "学习", "迁移", "教程", "项目", "AI"],
            "thesis": "做出来不代表已经学会。", "conditions": ["先明确目标。"], "does_not_imply": ["不要求停止使用工具。"],
            "diagnostic_questions": ["撤掉帮助能否迁移？"], "relations": [],
            "sources": [{"path": original.relative_to(root).as_posix(), "focus_terms": ["撤掉帮助", "迁移"], "role": "basis"},
                        {"path": contrast.relative_to(root).as_posix(), "focus_terms": ["长期拥有", "学习项目"], "role": "contrast"}]}
    pack = {"schema_version": 1, "snapshot_at": "2026-09-30", "evidence_role": "secondary-synthesis", "generation_method": "ai-written",
            "attribution_note": "AI 导航，不是独立来源。", "author": "AI", "publisher": "Test Publisher", "license": "CC-BY-4.0", "extra_public_metadata": "supported",
            "cards": cards or [card]}
    target = root / CARDS_PATH
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    refresh_manifest(root)
    shared = {"evidence_role": "published-source", "content_origin": "yuzheng-published-text", "generation_method": "not-established", "source_type": "community-post"}
    docs = [SimpleNamespace(**shared, id="original#chunk-1", source_id="original", path=card["sources"][0]["path"], source_family="original-family", text="背景没有关联。"),
            SimpleNamespace(**shared, id="original#chunk-2", source_id="original", path=card["sources"][0]["path"], source_family="original-family", text="撤掉帮助之后，换一道题才能检验迁移能力。"),
            SimpleNamespace(**shared, id="contrast#chunk-1", source_id="contrast", path=card["sources"][1]["path"], source_family="contrast-family", text=contrast.read_text())]
    return pack, docs


def refresh_manifest(root):
    files = [{"path": file.relative_to(root).as_posix(), "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "bytes": file.stat().st_size}
             for file in sorted(root.rglob("*")) if file.is_file() and file.name != "release-manifest.json"]
    (root / "release-manifest.json").write_text(json.dumps({"intended_visibility": "public", "files": files}), encoding="utf-8")


def rewrite_cards(root, pack):
    (root / CARDS_PATH).write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    refresh_manifest(root)


def test_missing_cards_is_optional_and_has_no_candidates(tmp_path):
    architecture = load(tmp_path, [])
    assert architecture.route("真的学会") == []
    assert architecture.linked_candidates([]) == []
    assert architecture.render_bundle([], []) == []


def test_hydration_selects_matching_primary_chunk_and_routes_specific_cues(tmp_path):
    _, docs = make_pack(tmp_path)
    architecture = load(tmp_path, docs)
    assert architecture.route("我做了几个项目，怎么知道自己真的学会了？") == ["learning-transfer"]
    assert architecture.route("离开教程，学习能否迁移到其他问题？") == ["learning-transfer"]
    assert architecture.route("AI项目怎么做？") == []
    assert architecture.route("世界杯谁获胜？") == []
    assert architecture.route("无关新闻？", history=[{"question": "真的学会", "summary": "迁移"}]) == []
    assert architecture.route("那我先试哪一步？", history=[{"question": "离开教程，学习能否迁移到其他问题？", "summary": ""}]) == ["learning-transfer"]
    assert architecture.linked_candidates(["learning-transfer"])[0] == {"docindex": 1, "weight": 1.0, "focus_terms": ["撤掉帮助", "迁移"]}


def test_bundle_maps_basis_and_contrast_to_actual_passages_only(tmp_path):
    _, docs = make_pack(tmp_path)
    architecture = load(tmp_path, docs)
    basis = Passage({"id": "S2", "excerpt": docs[1].text}, {"document_id": docs[1].id, "source_family": docs[1].source_family}, False)
    contrast = Passage({"id": "S4", "excerpt": docs[2].text}, {"docindex": 2}, False)
    assert architecture.render_bundle(["learning-transfer"], [contrast]) == []
    bundle = architecture.render_bundle(["learning-transfer"], [basis, contrast])
    assert bundle[0]["source_roles"] == [{"role": "basis", "source_ids": ["S2"]}, {"role": "contrast", "source_ids": ["S4"]}]
    assert bundle[0]["evidence_role"] == "secondary-synthesis"
    assert bundle[0]["author"] == "AI" and "source_ids" not in bundle[0]
    assert "extra_public_metadata" not in bundle[0]
    basis.discovery = True
    assert architecture.render_bundle(["learning-transfer"], [basis, contrast]) == []


def test_family_fallback_requires_actual_matching_evidence(tmp_path):
    _, docs = make_pack(tmp_path)
    architecture = load(tmp_path, docs)
    unrelated = Passage({"id": "S1", "excerpt": "介绍与主题无关的背景。"}, {"source_family": "original-family"}, False)
    assert architecture.render_bundle(["learning-transfer"], [unrelated]) == []
    unrelated.source["excerpt"] = docs[1].text
    assert architecture.render_bundle(["learning-transfer"], [unrelated])[0]["source_roles"] == [{"role": "basis", "source_ids": ["S1"]}]


@pytest.mark.parametrize("mutation", ["hash", "path", "missing", "relation", "duplicate", "no-basis", "many-refs", "many-cards", "bad-date", "bad-role", "empty-focus", "boolean-version"])
def test_invalid_public_cards_fail_closed(tmp_path, mutation):
    pack, docs = make_pack(tmp_path)
    card = pack["cards"][0]
    if mutation == "hash":
        (tmp_path / card["sources"][0]["path"]).write_text("Changed after pinning.")
    else:
        if mutation == "path": card["sources"][0]["path"] = "../outside.md"
        if mutation == "missing": card["sources"][0]["path"] = "corpus/community-posts/missing.md"
        if mutation == "relation": card["relations"] = [{"card_id": "does-not-exist", "kind": "contrast"}]
        if mutation == "duplicate": pack["cards"].append(card.copy())
        if mutation == "no-basis": card["sources"][0]["role"] = "case"
        if mutation == "many-refs": card["sources"] *= 5
        if mutation == "many-cards": pack["cards"] *= 65
        if mutation == "bad-date": pack["snapshot_at"] = "2026-90-40"
        if mutation == "bad-role": pack["evidence_role"] = "primary-speech"
        if mutation == "empty-focus": card["sources"][0]["focus_terms"] = []
        if mutation == "boolean-version": pack["schema_version"] = True
        rewrite_cards(tmp_path, pack)
    with pytest.raises(CardValidationError):
        load(tmp_path, docs)


@pytest.mark.parametrize("field,value", [("evidence_role", "secondary-synthesis"), ("evidence_role", "metadata-only"), ("generation_method", "ai-written"), ("content_origin", "ai-synthesis-of-mixed-sources"), ("source_type", "video-translation"), ("source_type", "video-catalog")])
def test_synthesis_translation_and_catalog_cannot_be_card_basis(tmp_path, field, value):
    _, docs = make_pack(tmp_path)
    setattr(docs[1], field, value)
    with pytest.raises(CardValidationError):
        load(tmp_path, docs)


@pytest.fixture(scope="module")
def actual_architecture():
    root = Path(__file__).resolve().parents[1] / "data/context"
    index = ContextIndex(root)
    index.load()
    return load(root, index.documents)


@pytest.mark.parametrize("question,expected", [
    ("我做出了几个 AI 项目，怎么知道自己是真的学会了？", "learning-transfer"),
    ("用AI提效做了很多汇报材料，工作却没有价值，怎么办？", "outcome-over-proxy"),
    ("demo能跑起来，为什么上线产品之后还要长期维护？", "production-ownership"),
    ("听了好建议还是执行不动，怎么提问才有帮助？", "advice-with-conditions"),
    ("面对新技术和热点，怎么找到自己任务的评价标准？", "task-owned-evaluation"),
    ("自媒体一直没人看，开始发视频前怎么选方向？", "personal-brand-amplification"),
    ("产品上线没人付费，怎么判断客户买的到底是什么？", "customer-pays-for-result"),
    ("良质和人生意义有什么关系？直觉可靠吗？", "quality-and-purpose"),
    ("不再跟着教程，换个需求就卡住，是学习错觉吗？", "learning-transfer"),
])
def test_actual_eight_topics_and_paraphrase_route(actual_architecture, question, expected):
    selected = actual_architecture.route(question)
    assert selected and selected[0] == expected
    assert len(selected) <= 3
    assert actual_architecture.linked_candidates(selected)


@pytest.mark.parametrize("question,expected", [
    ("我在纠结要不要跳槽，两个offer一个大厂一个创业公司", "zbs-career-layers"),
    ("怎么才能升职加薪？感觉自己被低估了", "zbs-personal-value"),
    ("如何找到适合写在简历里的项目", "zbs-personal-value"),
    ("每天工作很多，但成长很慢，都是杂事", "zbs-high-value-work"),
    ("老板不公平，我不是嫡系所以没机会", "zbs-thinking-traps"),
    ("想开始理财投资，怕当韭菜", "zbs-money-and-investing"),
    ("不知道选什么方向，感觉很迷茫", "zbs-craft-persistence"),
    ("想做副业但不好意思开口要钱", "zbs-make-money-skill"),
    ("怎么和老板沟通才能让他看到我的价值", "zbs-communication"),
])
def test_actual_course_topics_route_to_lesson_texts(actual_architecture, question, expected):
    selected = actual_architecture.route(question)
    assert selected and selected[0] == expected
    lessons = [row for row in actual_architecture.linked_candidates(selected[:1])
               if actual_architecture.documents[row["docindex"]].source_type == "course-lesson"]
    assert lessons and max(row["weight"] for row in lessons) == 1.0  # a lesson is the basis


@pytest.mark.parametrize("question", ["AI是什么？", "我有几个项目。", "今年世界杯谁获胜？", "哪里有好吃的火锅？"])
def test_actual_generic_and_unsupported_topics_do_not_select_cards(actual_architecture, question):
    assert actual_architecture.route(question) == []
