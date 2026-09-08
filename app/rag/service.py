'''Repeatable, evidence-preserving RAG ingestion and retrieval.'''

from dataclasses import replace
from pathlib import Path
import re
from typing import Sequence

from app.database.repository import HistoricalRecord, InMemoryRepository, KnowledgeRecord, Repository
from app.models.schemas import EvidenceItem
from app.providers.embeddings import HashEmbeddingProvider


class RetrievalResponse:
    def __init__(self, evidence: list[EvidenceItem], status: str, message: str | None = None):
        self.evidence = evidence
        self.status = status
        self.message = message


class RAGService:
    def __init__(self, repository: Repository, embedding_provider: object | None = None):
        self.repository = repository
        self.embedding_provider = embedding_provider or HashEmbeddingProvider()

    async def ingest_records(self, records: Sequence[KnowledgeRecord], chunk_size: int = 800) -> int:
        if chunk_size < 100:
            raise ValueError('chunk_size must be at least 100 characters')
        if not isinstance(self.repository, InMemoryRepository):
            raise NotImplementedError('PostgreSQL ingestion belongs to the database migration pass')
        existing = {record.id for record in self.repository.knowledge}
        added = 0
        for record in records:
            chunks = self._chunks(record.content, chunk_size)
            vectors = await self.embedding_provider.embed_documents(chunks)
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                chunk_id = f'{record.id}:{index}'
                if chunk_id in existing:
                    continue
                self.repository.knowledge.append(
                    replace(
                        record,
                        id=chunk_id,
                        document_id=record.document_id,
                        content=chunk,
                        embedding=tuple(vector),
                        metadata={**record.metadata, 'chunk_index': str(index)},
                    )
                )
                existing.add(chunk_id)
                added += 1
        return added

    async def retrieve(self, query: str, limit: int = 3) -> RetrievalResponse:
        query = query.strip()
        if not query:
            return RetrievalResponse([], 'no_evidence', 'query was empty')
        limit = max(1, min(limit, 5))
        vector = await self.embedding_provider.embed_query(query)
        rows = await self.repository.similarity_search(vector, limit * 2)
        terms = set(re.findall(r'[a-z0-9]+', query.lower()))
        scored: list[tuple[float, KnowledgeRecord]] = []
        for row in rows:
            content_terms = set(re.findall(r'[a-z0-9]+', row.content.lower()))
            lexical = len(terms & content_terms) / max(len(terms), 1)
            vector_score = self._cosine(vector, row.embedding or ())
            score = max(0.0, min(1.0, (0.65 * vector_score) + (0.35 * lexical)))
            if score >= 0.08:
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        evidence = [
            EvidenceItem(
                id=row.id,
                kind='runbook',
                title=row.title,
                excerpt=' '.join(row.content.split())[:800],
                score=round(score, 4),
            )
            for score, row in scored[:limit]
        ]
        status = 'complete' if evidence else 'no_evidence'
        message = None if evidence else 'no relevant runbook evidence found'
        return RetrievalResponse(evidence, status, message)

    async def history(self, service: str | None, category: str, limit: int = 3) -> list[HistoricalRecord]:
        return await self.repository.list_history(service, category, max(1, min(limit, 5)))

    @staticmethod
    def _chunks(content: str, chunk_size: int) -> list[str]:
        paragraphs = [part.strip() for part in re.split(r'\n{2,}', content) if part.strip()]
        chunks: list[str] = []
        current = ''
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 1 > chunk_size:
                chunks.append(current)
                current = ''
            current = f'{current} {paragraph}'.strip()
        if current:
            chunks.append(current)
        return chunks or [content[:chunk_size]]

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        left_norm = sum(value * value for value in left) ** 0.5
        right_norm = sum(value * value for value in right) ** 0.5
        if not left_norm or not right_norm:
            return 0.0
        return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)))
