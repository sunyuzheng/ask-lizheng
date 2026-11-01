"""Shared, deliberately limited follow-up default; not a topic classifier."""
import re

FOLLOWUP = re.compile(r"^(那|这|它|上面|刚才|继续)|具体怎么|举个例子|能展开")


def uses_previous_question(question: str, history: list | None) -> bool:
    return bool(history) and len(question) < 160 and bool(FOLLOWUP.search(question))
