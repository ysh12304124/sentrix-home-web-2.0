"""Conservative source-text context, never a claim that a reservation took place."""

from __future__ import annotations

import re
from datetime import datetime


def normalise_label(value: str) -> str:
    return "".join(c for c in re.sub(r"['’]s\b", "", value.casefold()) if c.isalnum())


def reservation_context(text: str) -> tuple[tuple[str, ...], str]:
    if not re.search(r"\b(reservation|booking|appointment)\b", text, re.I):
        return (), ""
    # Dataset headers describe the mail time, not the time of the activity.
    text = re.sub(r"^Date:.*$", "", text, flags=re.M)
    dates = set()
    patterns = (
        (r"\b\d{4}-\d{2}-\d{2}\b", "%Y-%m-%d"),
        (r"\b[A-Z][a-z]+ \d{1,2},? \d{4}\b", "%B %d %Y"),
        (r"\b\d{1,2} [A-Z][a-z]+ \d{4}\b", "%d %B %Y"),
    )
    for pattern, fmt in patterns:
        for raw in re.findall(pattern, text):
            try:
                dates.add(datetime.strptime(raw.replace(",", ""), fmt).date().isoformat())
            except ValueError:
                continue
    place = re.search(r"\bat ([A-Z][\w’\x27 .-]{1,70}?)\s+(?:on|for)\b", text)
    return tuple(sorted(dates)), place.group(1).strip() if place else ""
