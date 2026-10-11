"""Find the people a question names, from Open Context's shared name table.

catalog/guest-names.json (generated in Open Context) lists every name that finds a person, the
qualifiers that tell namesakes apart, the host's names, and the key rule. A name counts only where
it stands as a name in the question: "Sean Ellis" is not the guest Sean, 孙宇晨 is not the guest
孙宇. When a match is uncertain the question falls back to an ordinary search, which still finds
programmes by their titles and participants; a wrong person is worse than no person.
"""

from __future__ import annotations

import re
import unicodedata

HAN = re.compile(r"[㐀-鿿]")
LATIN_WORD = re.compile(r"[ \t]+([A-Za-z][A-Za-z'’-]*)")
# Lowercase English words that may follow a first name without being a surname.
FOLLOWING_WORDS = frozenset("""
a about all an and are as at be by did do does for from had has have he her his how i in interview interviews
is it of on or podcast said say says she show talk talked talks that the their them they think thinks this
to video videos vs was what when where who why will with
""".split())
# Characters that may stand next to a short Chinese name without making it part of a longer one.
HAN_AFTER = frozenset("的说讲谈聊认觉怎如和与跟及对在是有这那哪为吗呢吧啊么等们也都还就最曾之给被把老先女提问答观看想关做写呀哈嘛")
HAN_BEFORE = frozenset("和与跟及对在是有问请找叫听看读把给让被从向同访谈聊位个的了宾嘛呀吧")


class NameIndex:
    def __init__(self, table: dict):
        self.drop = frozenset(table["key_drop"])
        for text, key in table.get("key_examples", []):
            if self.key(text) != key:
                raise ValueError("Name table key rule disagrees with this reader")
        self.names = {row["key"]: row for row in table["names"]}
        self.qualifiers = {entity_id: [row["key"] for row in rows] for entity_id, rows in table.get("qualifiers", {}).items()}
        self.speakers = {row["speaker_id"]: [name["name"] for name in row["names"]] for row in table.get("speakers", [])}
        self.by_entity: dict[str, list[str]] = {}
        for row in table["names"]:
            for entity_id in row["entity_ids"]:
                self.by_entity.setdefault(entity_id, []).append(row["name"])

    @classmethod
    def from_catalog(cls, catalog: dict) -> "NameIndex":
        """Display names and aliases only, for a pack published before the name table."""
        drop = "!\"#&'(),-./:;<>?@[]_{|}·–—‘’“”•、。《》「」『』【】・"
        reader = cls({"key_drop": drop, "names": [], "speakers": [{"speaker_id": "yuzheng", "names": [{"name": "立正"}]}]})
        rows: dict[str, dict] = {}
        for entity in catalog.get("entities", []):
            for text in [entity["display_name"], *entity["aliases"]]:
                key = reader.key(text)
                if key:
                    row = rows.setdefault(key, {"name": text, "key": key, "entity_ids": []})
                    if entity["entity_id"] not in row["entity_ids"]:
                        row["entity_ids"].append(entity["entity_id"])
        return cls({"key_drop": drop, "names": list(rows.values()),
                    "speakers": [{"speaker_id": "yuzheng", "names": [{"name": "立正"}]}]})

    def key(self, text: str) -> str:
        return "".join(char for char in unicodedata.normalize("NFKC", text).casefold()
                       if char not in self.drop and not char.isspace())

    def names_of(self, entity_id: str) -> list[str]:
        return list(self.by_entity.get(entity_id, []))

    def speaker_names(self, speaker_id: str) -> list[str]:
        return list(self.speakers.get(speaker_id, []))

    def resolve(self, text: str) -> set[str]:
        """Entity IDs the text names, keeping namesakes apart by their qualifiers."""
        flat, origin = [], []
        for position, char in enumerate(text):
            for piece in unicodedata.normalize("NFKC", char).casefold():
                if piece not in self.drop and not piece.isspace():
                    flat.append(piece)
                    origin.append(position)
        flat = "".join(flat)
        spans = []
        for key, row in self.names.items():
            found = flat.find(key)
            while found != -1:
                start, end = origin[found], origin[found + len(key) - 1] + 1
                identities = self._standing(text, start, end, key, set(row["entity_ids"]))
                if identities:
                    spans.append((start, end, identities))
                found = flat.find(key, found + 1)
        kept = [span for span in spans
                if not any(a <= span[0] and b >= span[1] and b - a > span[1] - span[0] for a, b, _ in spans)]
        result: set[str] = set()
        for _, _, identities in kept:
            if len(identities) > 1:
                supported = {identity for identity in identities if self._qualified(identity, flat)}
                identities = supported or identities
            result |= identities
        return result

    def _qualified(self, identity: str, flat: str) -> bool:
        # Short Latin qualifiers (Bar, Meta) appear inside ordinary words; they do not decide.
        return any(key in flat for key in self.qualifiers.get(identity, ()) if len(key) >= 4 or not key.isascii())

    def _standing(self, text: str, start: int, end: int, key: str, identities: set[str]) -> set[str]:
        surface = text[start:end]
        if key.isascii():
            if start and text[start - 1].isascii() and text[start - 1].isalnum():
                return set()
            if end < len(text) and text[end].isascii() and text[end].isalnum():
                return set()
            if re.fullmatch(r"[A-Za-z0-9]+", surface):
                following = LATIN_WORD.match(text[end:])
                if following:
                    word = following.group(1)
                    if word[0].isupper() or word.casefold() not in FOLLOWING_WORDS:
                        # Another name follows: only a namesake whose qualifier it is stays.
                        word_key = self.key(word)
                        return {identity for identity in identities
                                if any(qualifier.startswith(word_key) for qualifier in self.qualifiers.get(identity, ()))}
            return identities
        if len(key) <= 3 and all(HAN.match(char) for char in key):
            if end < len(text) and HAN.match(text[end]) and text[end] not in HAN_AFTER:
                return set()
            if start and HAN.match(text[start - 1]) and text[start - 1] not in HAN_BEFORE:
                return set()
        return identities
