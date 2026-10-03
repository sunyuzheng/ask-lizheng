"""Validated public reasoning navigation, hydrated back to primary source chunks.

Cards are secondary synthesis, never an additional citable source. This module
has no network, private-context, cache-writing, or automatic observation paths.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


CARDS_PATH = "context/decision-cards.json"
# Primary refers to an actual source body, not whose position its speech proves.
# Speaker-attributed public transcripts retain their separate stance metadata.
PRIMARY_ROLES = {"primary-authored", "primary-speech", "speaker-attributed-speech", "published-source"}
GENERIC_CUES = set("ai 人工智能 项目 产品 用户 目标 工具 价值 标准 目的 感受 框架 比较 采用 学习 学会 效率 成果 报告 建议 提问 处境 阻力 人生 需求 付费 增长 维护 自建 quality learn tutorial question advice outcome productivity".split())
ROLE_WEIGHTS = {"basis": 1.0, "contrast": .9, "case": .65}


class CardValidationError(ValueError):
    """A fixed, non-content-bearing error for an invalid public projection."""


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=300)
    focus_terms: list[str] = Field(min_length=1, max_length=12)
    role: Literal["basis", "contrast", "case"]

    @field_validator("focus_terms")
    @classmethod
    def bounded_terms(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("Invalid focus terms")
        return list(dict.fromkeys(value.strip() for value in values))


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    card_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    kind: str = Field(min_length=1, max_length=60)


class DecisionCard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    title: str = Field(min_length=1, max_length=180)
    query_cues: list[str] = Field(min_length=1, max_length=40)
    thesis: str = Field(min_length=1, max_length=1800)
    conditions: list[str] = Field(max_length=12)
    does_not_imply: list[str] = Field(max_length=12)
    diagnostic_questions: list[str] = Field(max_length=6)
    relations: list[Relation] = Field(max_length=12)
    sources: list[SourceReference] = Field(min_length=1, max_length=8)

    @field_validator("query_cues", "conditions", "does_not_imply", "diagnostic_questions")
    @classmethod
    def bounded_strings(cls, values, info):
        bound = 100 if info.field_name == "query_cues" else 800
        if any(not value.strip() or len(value) > bound for value in values):
            raise ValueError("Invalid card text list")
        return list(dict.fromkeys(value.strip() for value in values))


class CardPack(BaseModel):
    # Public metadata, such as author/publisher/license, can evolve without
    # changing the core card schema. Unknown fields never enter model prompts.
    model_config = ConfigDict(extra="allow", strict=True)
    schema_version: Literal[1]
    snapshot_at: str = Field(min_length=10, max_length=35)
    evidence_role: Literal["secondary-synthesis"]
    generation_method: Literal["ai-written"]
    attribution_note: str = Field(min_length=1, max_length=1800)
    author: str = Field(default="AI", max_length=180)
    publisher: str = Field(default="", max_length=180)
    license: str = Field(default="", max_length=100)
    cards: list[DecisionCard] = Field(max_length=64)

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported card schema version")
        return value

    @field_validator("snapshot_at")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value[:10])
        return value


@dataclass(frozen=True)
class HydratedReference:
    docindex: int
    role: str
    focus_terms: tuple[str, ...]
    document_id: str
    source_family: str


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).lower()).strip()


def _focus_score(text: str, terms) -> float:
    text = _normalized(text)
    score = 0.0
    for raw in terms:
        term = _normalized(raw)
        if term in text:
            score += 4 + min(len(term), 12) / 4
        elif len(term) >= 4:
            # Partial matches select a nearby original passage; the card still
            # cannot be cited or used to supply missing evidence.
            pairs = set(re.findall(r"[\u3400-\u9fff]{2}", term))
            if pairs:
                score += .35 * sum(pair in text for pair in pairs) / len(pairs)
    return score


def _safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or str(path) != relative:
        raise CardValidationError("Invalid public source path")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise CardValidationError("Public source path is missing or escapes the pack")
    return target


def _verified_file(root: Path, relative: str, manifest: dict) -> Path:
    target = _safe_path(root, relative)
    record = manifest.get(relative)
    if not isinstance(record, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))):
        raise CardValidationError("Public source is absent from the release manifest")
    raw = target.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record["sha256"] or record.get("bytes", len(raw)) != len(raw):
        raise CardValidationError("Public source differs from the pinned release")
    return target


def _primary(doc) -> bool:
    role = str(getattr(doc, "evidence_role", ""))
    source_type = str(getattr(doc, "source_type", ""))
    origin = str(getattr(doc, "content_origin", "")).lower()
    method = str(getattr(doc, "generation_method", "")).lower()
    return (
        role in PRIMARY_ROLES and bool(getattr(doc, "text", "").strip())
        and source_type not in {"context", "video-translation"}
        and not source_type.endswith("-catalog")
        and not any(word in origin + " " + method for word in ("ai-written", "ai-synthesis", "ai-translation", "translation", "repost"))
    )


class ContextArchitecture:
    def __init__(self, documents, pack: CardPack | None = None, references: dict | None = None):
        self.documents = documents
        self.pack = pack
        self.cards = {card.id: card for card in pack.cards} if pack else {}
        self.references = references or {}

    def route(self, question: str, context: str = "", history: list | None = None) -> list[str]:
        if not self.cards:
            return []
        # The current question owns routing. Context can disambiguate it; old
        # turns alone cannot keep injecting a previous topic into every answer.
        if history and len(question) < 160 and re.search(r"^(那|这|它|上面|刚才|继续)|具体怎么|举个例子|能展开", question):
            last = history[-1]
            prior = last.get("question", "") if isinstance(last, dict) else getattr(last, "question", "")
            question += " " + str(prior)[:600]
        question = _normalized(question[:2000])
        context = _normalized(context[:2500])
        scored = []
        for card in self.cards.values():
            hits = []
            for cue in card.query_cues:
                cue = _normalized(cue)
                pattern = re.escape(cue)
                if re.fullmatch(r"[a-z0-9 +._-]+", cue):
                    pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
                for match in re.finditer(pattern, question):
                    hits.append((match.start(), match.end(), cue, 1.0))
                if not any(hit[2] == cue for hit in hits) and re.search(pattern, context):
                    hits.append((-1, -1, cue, .45))
            # Nested terms such as 教程 inside 跟着教程 are one signal.
            independent = []
            for hit in sorted(hits, key=lambda h: (-len(h[2]), h[0])):
                if hit[0] != -1 and any(hit[0] >= old[0] and hit[1] <= old[1] for old in independent if old[0] != -1):
                    continue
                if hit[2] not in {old[2] for old in independent}:
                    independent.append(hit)
            current = [hit for hit in independent if hit[3] == 1.0]
            specific = [hit for hit in independent if hit[2] not in GENERIC_CUES]
            distinctive = [hit for hit in current if hit[2] not in GENERIC_CUES and (hit[2] in {"良质"} or len(hit[2]) >= 4 or len(re.findall(r"[\u3400-\u9fff]", hit[2])) >= 3)]
            if not current or not specific or (not distinctive and len(independent) < 2):
                continue
            score = sum((1.0 + min(len(hit[2]), 12) / 4) * hit[3] for hit in independent)
            scored.append((score, card.id))
        return [card_id for _, card_id in sorted(scored, key=lambda row: (-row[0], row[1]))[:3]]

    def linked_candidates(self, card_ids: list[str]) -> list[dict]:
        candidates = {}
        for card_id in card_ids[:3]:
            for reference in self.references.get(card_id, ()):
                row = candidates.setdefault(reference.docindex, {"docindex": reference.docindex, "weight": 0.0, "focus_terms": []})
                row["weight"] = max(row["weight"], ROLE_WEIGHTS[reference.role])
                row["focus_terms"] = list(dict.fromkeys(row["focus_terms"] + list(reference.focus_terms)))
        return sorted(candidates.values(), key=lambda row: (-row["weight"], row["docindex"]))

    def render_bundle(self, card_ids: list[str], passages: list) -> list[dict]:
        result = []
        for card_id in card_ids[:3]:
            card = self.cards.get(card_id)
            if card is None:
                continue
            mapped = []
            for reference in self.references[card_id]:
                ids = []
                for passage in passages:
                    if passage.discovery:
                        continue
                    evidence = passage.evidence
                    exact = evidence.get("document_id") == reference.document_id or evidence.get("docindex") == reference.docindex
                    fallback = not ("document_id" in evidence or "docindex" in evidence) and evidence.get("source_family") == reference.source_family and _focus_score(passage.source.get("excerpt", ""), reference.focus_terms) >= 4
                    if exact or fallback:
                        ids.append(passage.source["id"])
                if ids:
                    mapped.append({"role": reference.role, "source_ids": list(dict.fromkeys(ids))})
            if not any(row["role"] == "basis" for row in mapped):
                continue
            result.append({
                "id": card.id, "title": card.title, "thesis": card.thesis,
                "conditions": card.conditions, "does_not_imply": card.does_not_imply,
                "diagnostic_questions": card.diagnostic_questions,
                "relations": [relation.model_dump() for relation in card.relations if relation.card_id in card_ids],
                "source_roles": mapped, "snapshot_at": self.pack.snapshot_at,
                "evidence_role": self.pack.evidence_role, "generation_method": self.pack.generation_method,
                "attribution_note": self.pack.attribution_note,
                "author": self.pack.author, "publisher": self.pack.publisher,
            })
        return result


def load(root: Path, documents) -> ContextArchitecture:
    root = Path(root)
    if not (root / CARDS_PATH).is_file():
        return ContextArchitecture(documents)
    try:
        raw_manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
        records = raw_manifest["files"]
        manifest = {record["path"]: record for record in records}
        if len(manifest) != len(records):
            raise CardValidationError("Duplicate paths in the public release manifest")
        card_path = _verified_file(root, CARDS_PATH, manifest)
        if card_path.stat().st_size > 512_000:
            raise CardValidationError("Public card pack exceeds its size limit")
        pack = CardPack.model_validate_json(card_path.read_text(encoding="utf-8"))
    except CardValidationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, ValidationError):
        raise CardValidationError("Public card pack or release manifest is invalid") from None
    cards = {card.id: card for card in pack.cards}
    if len(cards) != len(pack.cards):
        raise CardValidationError("Duplicate public reasoning card IDs")
    by_path = {}
    for index, doc in enumerate(documents):
        by_path.setdefault(doc.path, []).append(index)
    verified = set()
    hydrated = {}
    for card in pack.cards:
        if not any(reference.role == "basis" for reference in card.sources):
            raise CardValidationError("A public reasoning card needs a primary basis")
        if any(relation.card_id not in cards or relation.card_id == card.id for relation in card.relations):
            raise CardValidationError("Public reasoning card has a broken relation")
        if len({reference.path for reference in card.sources}) != len(card.sources):
            raise CardValidationError("Duplicate source references in a public reasoning card")
        hydrated[card.id] = []
        for reference in card.sources:
            if reference.path not in verified:
                _verified_file(root, reference.path, manifest)
                verified.add(reference.path)
            candidates = by_path.get(reference.path, [])
            if not candidates or any(not _primary(documents[index]) for index in candidates):
                raise CardValidationError("Reasoning card references a non-primary or missing body")
            scored = [(_focus_score(documents[index].text, reference.focus_terms), index) for index in candidates]
            score, index = max(scored, key=lambda row: (row[0], -row[1]))
            if score <= 0:
                raise CardValidationError("Reasoning card focus has no matching primary passage")
            doc = documents[index]
            hydrated[card.id].append(HydratedReference(index, reference.role, tuple(reference.focus_terms), doc.id, doc.source_family or doc.source_id))
    return ContextArchitecture(documents, pack, hydrated)
