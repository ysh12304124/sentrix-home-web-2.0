"""Additive interactive API contracts; raw product traces retain their existing fields."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ModelSelection(BaseModel):
    profile: str = "__current__"
    target_id: str = ""
    base_url: str = ""
    manager_url: str = ""
    endpoint_model: str = ""


class RetestRequest(BaseModel):
    qa_id: str = Field(min_length=1)
    reasoning: StrictBool = Field(default=True, description="是否开启推理模式，默认开启")
    model: ModelSelection | None = None
    scoring_profile: str = ""
    scoring_batch: str = ""


class RetestResponse(BaseModel):
    run_id: str
    retest_context: dict[str, Any]


class ChatCreateRequest(BaseModel):
    scope_id: str = Field(min_length=1)
    reasoning: StrictBool = Field(default=True, description="是否开启推理模式，默认开启")
    model: ModelSelection | None = None
    message: str = Field(default="", max_length=2000)
    album_id: str = ""
    qa_set: str = ""
    qa_id: str = ""


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    reasoning: StrictBool | None = Field(default=None, description="本轮推理模式；省略时沿用会话设置")


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    session_id: str
    scope_id: str
    status: str
    reasoning: bool | None = None
    source: dict[str, Any] | None = None
    turns: list[dict[str, Any]]
    items: list[dict[str, Any]] | None = None
