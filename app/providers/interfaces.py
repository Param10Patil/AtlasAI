"""Provider protocols with no vendor-specific import at the domain boundary."""

from collections.abc import Sequence
from typing import Protocol


class LLMProvider(Protocol):
    async def generate(self, prompt: str, *, timeout_seconds: float) -> str: ...


class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...
