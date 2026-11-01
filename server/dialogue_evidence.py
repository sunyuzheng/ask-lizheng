"""Contiguous source windows and mechanical speaker eligibility, never endorsement inference."""
from __future__ import annotations

ATTRIBUTED = {"reviewed-source-attribution", "audio-voice-match"}
NON_STANCE = {"question", "introduction", "acknowledgement", "joke", "reported-other"}
ACT_LABELS = {"statement":"陈述", "question":"提问", "introduction":"介绍", "acknowledgement":"回应",
              "joke":"玩笑", "reported-other":"转述", "unclassified":"未分类"}


def supports_speaker(evidence, speaker_id, *, stance=True):
    units = evidence.get("dialogue_units")
    if units:
        return any(u.get("speaker_id") == speaker_id and u.get("speaker_status") in ATTRIBUTED
                   and (not stance or u.get("speech_act") == "statement") for u in units)
    return (evidence.get("speaker_id") == speaker_id and evidence.get("speaker_status") in ATTRIBUTED
            and (not stance or evidence.get("speech_act") not in NON_STANCE))


def window(units, terms, weights, budget=2400, *, center_time=None):
    """Return whole adjacent cues around the best lexical hit; never join distant turns."""
    def value(u):
        text = u["text"].lower()
        return sum(weights.get(t, 1) * min(text.count(t), 2) for t in terms)
    best = (min(range(len(units)), key=lambda i: abs(units[i]["start_seconds"] - center_time))
            if center_time is not None else max(range(len(units)), key=lambda i: value(units[i])))
    start, end = best, best + 1
    size = len(units[best]["text"])
    while start > 0 or end < len(units):
        # Balanced surrounding context preserves both the question and response.
        choices = ([start - 1] if start else []) + ([end] if end < len(units) else [])
        added = False
        for i in choices:
            n = len(units[i]["text"])
            if size + n > budget:
                continue
            if i < start: start = i
            else: end = i + 1
            size += n
            added = True
        if not added: break
    return units[start:end]


def render(units, *, compact=False):
    groups = []
    for u in units:
        known = u.get("speaker_status") in ATTRIBUTED and u.get("speaker_id") not in {None, "", "unknown"}
        name = (u.get("speaker_name") or u["speaker_id"]) if known else "说话人待定"
        method = "声纹自动比对" if u.get("speaker_status") == "audio-voice-match" else "来源文字归属已核对" if known else "未确认"
        act = u.get("speech_act", "未分类")
        key = (name, method, ACT_LABELS.get(act, act))
        if groups and groups[-1][0] == key:
            groups[-1][2].append(u["text"])
        else:
            groups.append((key, int(u["start_seconds"]), [u["text"]]))
    def label(key):
        if not compact: return f"{key[0]}（{key[1]}；{key[2]}）"
        if key[1] == "未确认": return "待定"
        method = "声纹" if key[1] == "声纹自动比对" else "文字"
        return f"{key[0]}/{method}/{key[2]}"
    return "\n".join(f"[{s // 3600:02}:{s % 3600 // 60:02}:{s % 60:02}] {label(key)}：" + " ".join(text)
                     for key, s, text in groups)
