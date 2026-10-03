"""Retrieve a bounded set of attributable passages from the pinned public pack."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, quote

from .semantic import MIN_SIMILARITY

LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
TIMESTAMP = re.compile(r"\[(\d{2}:\d{2}:\d{2})\]\((https://www\.youtube\.com/watch\?[^)]+)\)")
STOP_TERMS = set("怎么 如何 什么 为什么 一个 这个 那个 可以 我们 他们 自己 现在 时候 问题 是否 哪些 有没有 应该 需要 如果 我的 你的 以及 然后 帮我 知道 想要 还是 能够 请问 关于 了解 看看 怎么办 告诉 几个 真的 会了 是真 的学 了几 我做 出了 我是".split())
SOURCE_ACCESS_FIELDS = (
    "source_visibility", "text_access", "membership_platform", "membership_url",
    "membership_verified_at", "transcript_source_kind", "transcript_quality",
    "speaker_classification",
)
QUESTION_FILLERS = re.compile("|".join(sorted(("我想", "我用", "我在", "我觉得", "我感觉", "但感觉", "不知道", "感觉", "应该", "怎么", "如何", "怎么办", "什么", "为什么", "我自己", "自己", "帮我", "请问", "能不能", "有没有", "一个", "很多", "但是", "但", "是否", "现在", "以后"), key=len, reverse=True)))
CONCEPTS = (
    (("学会", "学习", "教程", "真学", "学到", "learn"), "假学习 学习方法 迁移 撤掉帮助 guided mistakes"),
    (("效率", "工作价值", "fake work", "提效"), "fake work 成果 价值 表演 组织 激励"),
    (("demo", "原型", "产品", "继续做"), "demo production 需求验证 ownership 维护 用户 Don't build"),
    (("自媒体", "个人品牌", "内容创作", "创作", "表达欲"), "自媒体 放大 成功标准 个人品牌 表达欲 分发"),
    (("建议", "提问", "问问题"), "建议 处境 隐含前提 world model 提问 条件"),
    (("良质", "quality"), "良质 价值 判断 感受 标准"),
    (("转行", "转岗", "职业", "求职", "找工作", "career"), "职业 选择 能力 作品 求职 迁移"),
    (("面试", "interview"), "面试 表达 故事 证据 价值"),
    (("学习", "学不", "learn", "入门"), "学习 实践 反馈 真本事 练习"),
    (("拖延", "执行", "行动", "完美主义"), "行动 反馈 实践 交付 作品"),
    (("汇报", "沟通", "表达", "communication"), "沟通 表达 受众 目标 汇报"),
    (("升职", "晋升", "绩效", "promotion"), "晋升 价值 影响力 绩效 证据"),
    (("ai", "人工智能", "agent", "智能体"), "AI 工具 判断 能力 实践 context"),
    (("创业", "产品", "商业", "business"), "产品 用户 需求 价值 验证"),
    (("context", "上下文", "语境"), "context 上下文 资料 判断 经验"),
    (("选择", "决策", "纠结", "decision"), "决策 选择 信息 代价 价值"),
)


def plain_text(text: str) -> str:
    text = LINK.sub(lambda match: match.group(1), text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*[#>*]+\s*", "", text)
    return re.sub(r"\s+", " ", text.replace("`", "").replace("**", "")).strip()


def is_discovery(doc) -> bool:
    return doc.source_type.endswith("-catalog") or doc.evidence_role in {"metadata-only", "discovery-only"}


@dataclass
class Passage:
    source: dict
    evidence: dict
    discovery: bool


class ContextIndex:
    def __init__(self, root: Path, require_lock: bool = False):
        self.root = root
        self.require_lock = require_lock
        self.documents = []
        self.manifest = {}
        self.search_module = None
        self._texts = []
        self._titles = []
        self.architecture = None
        self.source_revision = "main"

    def load(self) -> None:
        lock_path = self.root.parent / "context-lock.json"
        if self.require_lock and not lock_path.is_file():
            raise ValueError("Public context lock is required")
        if lock_path.is_file():
            # Verify the pinned release before importing its public search code.
            from .semantic import verify_public_pack
            lock = json.loads(lock_path.read_text())
            actual = hashlib.sha256((self.root / "release-manifest.json").read_bytes()).hexdigest()
            if lock.get("release_manifest_sha256") != actual:
                raise ValueError("Public context lock does not match its release")
            verify_public_pack(self.root)
            revision = str(lock.get("source_commit", ""))
            if re.fullmatch(r"[a-f0-9]{40}", revision):
                self.source_revision = revision
        script = self.root / "scripts" / "search.py"
        if not script.is_file():
            raise FileNotFoundError("Public context pack is not available")
        name = "ask_lizheng_public_search_" + str(abs(hash(str(self.root))))
        spec = importlib.util.spec_from_file_location(name, script)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        module.ROOT = self.root
        self.search_module = module
        self.documents = module.load_documents()
        self._texts = [plain_text(doc.text).lower() for doc in self.documents]
        self._titles = [(doc.title + " " + doc.section).lower() for doc in self.documents]
        manifest = self.root / "release-manifest.json"
        self.manifest = json.loads(manifest.read_text()) if manifest.is_file() else {}
        from .context_architecture import load
        self.architecture = load(self.root, self.documents)

    def metadata(self) -> dict:
        counts = dict(self.manifest.get("counts") or {})
        counts["retrieval_chunks"] = len(self.documents)
        counts["source_families"] = len({doc.source_family or doc.source_id for doc in self.documents})
        counts["reasoning_cards"] = len(self.architecture.cards) if self.architecture else 0
        return {"context_date": self.manifest.get("snapshot_at", ""), "counts": counts}

    def retrieve(self, question: str, context: str = "", history: list | None = None, limit: int = 16, semantic_candidates: list | None = None) -> list[Passage]:
        if not self.documents:
            return []
        query = question.strip()
        history = history or []
        def field(item, key):
            return str(item.get(key, "")) if isinstance(item, dict) else str(getattr(item, key, ""))
        prior = " ".join(field(item, "question") + " " + field(item, "summary") for item in history[-2:])
        if history and len(query) < 160 and re.search(r"^(那|这|它|上面|刚才|继续)|具体怎么|举个例子|能展开", query):
            query += " " + field(history[-1], "question")[:500]
        card_ids = self.card_ids(question, context, history, semantic_candidates)
        linked = self.architecture.linked_candidates(card_ids) if self.architecture else []
        auxiliary = (context + " " + prior)[:1200]
        module = self.search_module
        focused_query = QUESTION_FILLERS.sub(" ", query[:600])
        primary = [term for term in module.query_terms(focused_query) if term not in STOP_TERMS][:70]
        extra = [term for term in module.query_terms(auxiliary) if term not in STOP_TERMS][:45]
        aliases = " ".join(expansion for cues, expansion in CONCEPTS if any(cue in (query + " " + context).lower() for cue in cues))
        expanded = [term for term in module.query_terms(aliases) if term not in STOP_TERMS][:45]
        terms = list(dict.fromkeys(primary + extra + expanded))
        if not primary and not linked and not semantic_candidates:
            return []
        frequencies = {term: sum(term in body or term in title for body, title in zip(self._texts, self._titles)) for term in terms}
        count = len(self.documents)
        average_length = sum(len(text) for text in self._texts) / max(count, 1)
        weights = {term: math.log(1 + (count - frequency + .5) / (frequency + .5)) for term, frequency in frequencies.items()}
        # Full question clauses earn a coverage bonus, rather than letting one
        # incidental noun select every candidate for a multi-part question.
        clauses = [part.strip() for part in re.split(r"[，。！？、；\n]|(?:以及|同时|但是)", query) if len(part.strip()) >= 4][:5]
        clause_terms = [set(module.query_terms(clause)) - STOP_TERMS for clause in clauses]
        ranked = []
        for index, (doc, body, title) in enumerate(zip(self.documents, self._texts, self._titles)):
            matched = [term for term in primary if term in body or term in title]
            if not matched:
                continue
            if len(primary) > 3 and len(matched) < 2 and not any(term in title and len(term) > 2 for term in matched):
                continue
            def term_score(term):
                frequency = body.count(term)
                normalized = frequency * 2.35 / (frequency + 1.35 * (.22 + .78 * max(len(body), 1) / max(average_length, 1))) if frequency else 0
                return weights[term] * ((5 if term in title else 0) + normalized)
            score = sum(term_score(term) for term in matched)
            score += .35 * sum(weights[term] * ((3 if term in title else 0) + min(body.count(term), 2)) for term in extra if term in body or term in title)
            score += .65 * sum(term_score(term) for term in expanded if term in body or term in title)
            coverage = sum(bool(chunk.intersection(matched)) for chunk in clause_terms)
            score *= 1 + .12 * max(0, coverage - 1)
            if doc.content_status != "current":
                score *= .5
            if is_discovery(doc):
                score *= .32
            if doc.source_type == "context" or doc.evidence_role == "secondary-synthesis":
                score *= .7
            # News and logistics remain discoverable, but weak incidental
            # matches should not outrank substantive material.
            if re.search(r"报名|名额|截止|活动通知|课程通知|开课|优惠|直播预告", doc.title):
                score *= .68
            if re.search(r"Main Community｜|Knowledge Bank｜|访谈过的.*列表|报名指南|产品入口", doc.title) and not re.search(r"社区|课程|产品|入口|报名|community|knowledge bank", query, flags=re.I):
                score *= .35
            ranked.append((score, index))
        ranked.sort(key=lambda row: (-row[0], self.documents[row[1]].title))
        # Reciprocal rank fusion preserves exact-word matches while admitting
        # paraphrases. Curated links bring the basis and scope contrast together.
        fused = {index: 1 / (40 + rank) for rank, (_, index) in enumerate(ranked, 1)}
        for rank, candidate in enumerate(semantic_candidates or [], 1):
            index = candidate["docindex"]
            if 0 <= index < len(self.documents) and candidate["score"] >= MIN_SIMILARITY:
                fused[index] = fused.get(index, 0) + 1 / (40 + rank)
        focus_by_index = {}
        for candidate in linked:
            index = candidate["docindex"]
            focus_by_index[index] = candidate["focus_terms"]
            fused[index] = fused.get(index, 0) + .055 * candidate["weight"]
        ranked = sorted(((score, index) for index, score in fused.items()), key=lambda row: (-row[0], self.documents[row[1]].title))
        seen = set()
        passages = []
        for score, index in ranked:
            doc = self.documents[index]
            family = doc.source_family or doc.source_id
            if family in seen:
                continue
            url = doc.original_source_url if doc.source_type == "video-translation" and doc.original_source_url else doc.source_url
            if urlparse(url).scheme not in {"http", "https"}:
                continue
            seen.add(family)
            focused = [term.lower() for term in focus_by_index.get(index, [])]
            excerpt_terms = list(dict.fromkeys(primary + expanded + focused))
            excerpt_weights = {term: weights.get(term, 4) * (1 if term in primary else .45) for term in excerpt_terms}
            for term in focused:
                excerpt_weights[term] = max(8, excerpt_weights.get(term, 0))
            excerpt, timestamp, timestamp_url = self._excerpt(doc, excerpt_terms, excerpt_weights)
            if timestamp_url:
                url = timestamp_url
            source_id = "S" + str(len(passages) + 1)
            reason = "可回到原文核对这部分判断"
            if is_discovery(doc):
                reason = "标题或嘉宾信息相关；只可用于找资料，未收录正文"
            source = {
                "id": source_id, "title": doc.title, "url": url,
                "date": doc.original_published_at or doc.published_at,
                "excerpt": excerpt, "author": doc.original_author or doc.author or "作者未标明",
                "source_type": doc.source_type, "reason": reason,
                "attribution_note": doc.attribution_note,
                "evidence_role": doc.evidence_role or ("metadata-only" if is_discovery(doc) else "unclassified"),
            }
            # These verified corpus fields describe the original video and its
            # separately public transcript; model output never owns access labels.
            for key in SOURCE_ACCESS_FIELDS:
                value = getattr(doc, key, "")
                if isinstance(value, str) and value:
                    source[key] = value
            if not is_discovery(doc) and doc.path.startswith(("corpus/", "context/", "examples/")):
                source["public_copy_url"] = "https://github.com/sunyuzheng/lizheng-open-context/blob/" + self.source_revision + "/" + quote(doc.path, safe="/")
            if timestamp:
                source["timecode"] = timestamp
            evidence = {**source, "document_id": doc.id, "docindex": index, "source_path": doc.path, "source_family": family, "source_context": doc.source_context,
                        "yuzheng_stance_weight": doc.yuzheng_stance_weight,
                        "content_origin": doc.content_origin, "publisher": doc.publisher,
                        "generation_method": doc.generation_method,
                        "rights_scope": getattr(doc, "rights_scope", ""), "license": getattr(doc, "license", ""),
                        "discovery_only": is_discovery(doc), "section": doc.section}
            passages.append(Passage(source, evidence, is_discovery(doc)))
            if len(passages) >= limit:
                break
        return passages

    def card_ids(self, question, context, history, semantic_candidates=None):
        if not self.architecture:
            return []
        selected = self.architecture.route(question, context, history)
        if selected:
            return selected
        # A user need not know a framework's keywords. Strong semantic hits on
        # its actual primary basis can route to its scope and contrast sources.
        paths = {self.documents[row["docindex"]].path: row["score"] for row in (semantic_candidates or [])[:8] if row["score"] >= .4}
        scored = []
        for card in self.architecture.cards.values():
            score = max((paths.get(ref.path, 0) for ref in card.sources if ref.role == "basis"), default=0)
            if score:
                scored.append((score, card.id))
        return [identity for _, identity in sorted(scored, key=lambda row: (-row[0], row[1]))[:3]]

    def reasoning_bundle(self, question, context, history, passages, semantic_candidates=None):
        if not self.architecture:
            return []
        return self.architecture.render_bundle(self.card_ids(question, context, history, semantic_candidates), passages)

    def _excerpt(self, doc, terms: list[str], weights: dict[str, float]) -> tuple[str, str, str]:
        paragraphs = [(match.start(), match.group()) for match in re.finditer(r"\S[^\n]*(?:\n(?!\s*\n)[^\n]*)*", doc.text)]
        if not paragraphs:
            return plain_text(doc.text)[:1800], "", ""
        def value(row):
            text = plain_text(row[1]).lower()
            return sum(weights.get(term, 1) * min(text.count(term), 2) for term in terms)
        best = max(range(len(paragraphs)), key=lambda index: value(paragraphs[index]))
        start, end = max(0, best - 1), best + 1
        size = sum(len(plain_text(row[1])) for row in paragraphs[start:end])
        # Timed speech uses one short paragraph per caption. Include the
        # surrounding thought, not merely the matching caption or video intro.
        while size < 1500 and (start > 0 or end < len(paragraphs)):
            if end < len(paragraphs):
                size += len(plain_text(paragraphs[end][1])); end += 1
            if start > 0 and size < 1500:
                start -= 1; size += len(plain_text(paragraphs[start][1]))
        raw = "\n\n".join(row[1] for row in paragraphs[start:end])
        plain = plain_text(raw)
        if len(plain) > 2100:
            focused = plain_text(paragraphs[best][1])
            lowered = focused.lower()
            matching = [(weights.get(term, 1), lowered.find(term)) for term in terms if term in lowered]
            position = max(matching)[1] if matching else 0
            offset = plain.find(focused) + position
            clip_start = max(0, offset - 650)
            excerpt = ("…" if clip_start else "") + plain[clip_start:clip_start + 2100]
        else:
            excerpt = plain
        best_position = paragraphs[best][0]
        time_matches = [match for match in TIMESTAMP.finditer(doc.text) if match.start() <= best_position + len(paragraphs[best][1])]
        anchor = time_matches[-1] if time_matches else None
        return excerpt, anchor.group(1) if anchor else "", anchor.group(2) if anchor else ""
