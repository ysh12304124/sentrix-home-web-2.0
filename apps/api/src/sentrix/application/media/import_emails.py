"""ATM email-format adapter into the shared text-memory use case."""

from collections.abc import Sequence

from sentrix.application.media.import_text import ImportTextMemories
from sentrix.domain.media.email import EmailItem


class ImportEmails(ImportTextMemories):
    def execute(self, scope_id: str, rows: Sequence[dict[str, object]]) -> dict[str, object]:
        emails = [EmailItem.parse(row) for row in rows]
        return super().execute(
            scope_id,
            [
                {
                    "external_id": e.external_id,
                    "recorded_at": e.timestamp,
                    "summary": e.summary,
                    "text": e.body,
                    "metadata": {"source_type": "email"},
                }
                for e in emails
            ],
        )
