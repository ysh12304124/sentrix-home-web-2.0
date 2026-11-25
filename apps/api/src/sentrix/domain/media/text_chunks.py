from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str
    start: int
    end: int
    protocol: str
