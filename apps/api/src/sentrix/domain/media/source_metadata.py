"""来源提供的文档信息与地址；与模型生成的观察分开保存。"""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    document_type: str = ""
    title: str = ""
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    precise_address: str = ""
    address_source: str = ""
    address_source_sha256: str = ""

    def __post_init__(self) -> None:
        for name in (
            "document_type",
            "title",
            "summary",
            "precise_address",
            "address_source",
            "address_source_sha256",
        ):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} 必须为字符串")
        if self.document_type not in {"", "email", "document"} or not isinstance(self.metadata, dict):
            raise ValueError("文档类型或来源 metadata 无效")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceMetadata":
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})
