"""JSON email import, canonical content and observable index status."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sentrix.api.deps import get_container
from sentrix.api.errors import InvalidRequestError, NotFoundError
from sentrix.api.routes.text import TextImportRequest, import_items, text_detail
from sentrix.bootstrap.container import Container
from sentrix.domain.media.email import EmailItem

router = APIRouter(tags=["emails"])


class EmailImportRequest(BaseModel):
    scope_id: str = Field(min_length=1, max_length=200)
    items: list[dict[str, object]] = Field(min_length=1, max_length=500)
    rebuild_graph: bool = True


@router.post("/emails/import")
def import_emails(payload: EmailImportRequest, container: Container = Depends(get_container)) -> dict:
    try:
        rows = [EmailItem.parse(row) for row in payload.items]
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return import_items(
        TextImportRequest(
            scope_id=payload.scope_id,
            rebuild_graph=payload.rebuild_graph,
            items=[
                {
                    "external_id": e.external_id,
                    "recorded_at": e.timestamp,
                    "summary": e.summary,
                    "text": e.body,
                    "metadata": {"source_type": "email"},
                }
                for e in rows
            ],
        ),
        container,
    )


@router.get("/emails/{asset_id}")
def email_detail(asset_id: str, container: Container = Depends(get_container)) -> dict:
    result = text_detail(asset_id, container)
    if result["observation"]["source_metadata"].get("source_type") != "email":
        raise NotFoundError("该文本来源不是邮件")
    return result
