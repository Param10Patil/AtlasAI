"""Small RAG contracts; storage and embedding implementations come later."""

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=120)
    chunk_id: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    source: str = Field(min_length=1, max_length=300)
    embedding: list[float] | None = None


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)

