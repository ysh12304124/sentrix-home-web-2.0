from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from sentrix.api.schemas.documents import DocumentImportResponse
from sentrix.api.schemas.media import ImportResponse


class UnifiedImportResponse(BaseModel):
    routed_to: Literal["document", "media"]
    result: DocumentImportResponse | ImportResponse
