"""Source-derived relation keys; no question or benchmark facts enter the graph."""

import re
from collections.abc import Mapping


def source_link_keys(body: str, metadata: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    keys: set[tuple[str, str]] = set()
    for field in ("thread_id", "in_reply_to", "message_id", "order_id", "booking_id", "reservation_id"):
        value = str(metadata.get(field) or "").strip().casefold()
        if value:
            keys.add(("thread" if field in {"in_reply_to", "message_id", "thread_id"} else field, value))
    subject = re.search(r"^Subject:\s*(.+)$", body, re.I | re.M)
    if subject:
        title = re.sub(r"^(?:(?:re|fw|fwd)\s*:\s*)+", "", subject[1], flags=re.I)
        title = re.sub(r"\W+", " ", title.casefold()).strip()
        if len(title.split()) >= 3:
            keys.add(("subject", title))
    for match in re.finditer(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,}){1,4}\b", body):
        words = match[0].casefold().split()
        for left, right in zip(words, words[1:], strict=False):
            keys.add(("entity", left + " " + right))
        if len(keys) >= 40:
            break
    for match in re.finditer(
        r"\b(order|booking|reservation|confirmation)\s*(?:reference|number|id|code|no\.?)"
        r"\s*(?:is\s*)?[:#-]?\s*([A-Z0-9][A-Z0-9-]{3,63})\b",
        body,
        re.I,
    ):
        value = match[2].casefold()
        if any(c.isdigit() for c in value):
            keys.add(("reference", value))
    return tuple(sorted(keys, key=lambda p: (p[0] in {"subject", "entity"}, p)))[:64]
