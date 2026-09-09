'''Repeatable, evidence-preserving RAG ingestion and retrieval.'''

import re
from collections.abc import Sequence
from dataclasses import replace

from app.database.repository import (
    HistoricalRecord,
    InMemoryRepository,
    KnowledgeRecord,
    PostgresRepository,
    Repository,
)
from app.models.schemas import EvidenceItem
from app.providers.embeddings import HashEmbeddingProvider


class RetrievalResponse:
    def __init__(
        self,
        evidence: list[EvidenceItem],
        status: str,
        message: str | None = None,
        *,
        retrieval_method: str = 'vector_cosine_plus_lexical_rerank',
        best_score: float = 0.0,
        candidate_count: int = 0,
    ):
        self.evidence = evidence
        self.status = status
        self.message = message
        self.retrieval_method = retrieval_method
        self.best_score = best_score
        self.candidate_count = candidate_count


class RAGService:
    def __init__(self, repository: Repository, embedding_provider: object | None = None):
        self.repository = repository
        self.embedding_provider = embedding_provider or HashEmbeddingProvider()

    async def ingest_records(self, records: Sequence[KnowledgeRecord], chunk_size: int = 800) -> int:
        if chunk_size < 100:
            raise ValueError('chunk_size must be at least 100 characters')
        if isinstance(self.repository, PostgresRepository):
            embedded: list[KnowledgeRecord] = []
            for record in records:
                chunks = self._chunks(record.content, chunk_size)
                vectors = await self.embedding_provider.embed_documents(chunks)
                for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                    embedded.append(replace(record, id=f'{record.id}:{index}', content=chunk, embedding=tuple(vector), metadata={**record.metadata, 'chunk_index': str(index)}))
            await self.repository.upsert_knowledge(embedded)
            return len(embedded)
        if not isinstance(self.repository, InMemoryRepository):
            raise TypeError('repository does not support ingestion')
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
        terms = self._terms(query)
        scored: list[tuple[float, KnowledgeRecord]] = []
        for row in rows:
            content_terms = self._terms(row.content)
            title_terms = self._terms(row.title)
            overlap = terms & (content_terms | title_terms)
            # A vector collision must not make an unrelated document look
            # relevant in the credential-free local index. External semantic
            # providers can still capture synonyms, while local retrieval
            # requires at least one grounded term from the incident query.
            if not overlap:
                continue
            lexical = len(terms & content_terms) / max(len(terms), 1)
            title_overlap = len(terms & title_terms) / max(len(terms), 1)
            phrase_bonus = 0.08 if any(term in row.content.lower() for term in terms if len(term) > 4) else 0.0
            vector_score = self._cosine(vector, row.embedding or ())
            score = max(0.0, min(1.0, (0.55 * vector_score) + (0.30 * lexical) + (0.15 * title_overlap) + phrase_bonus))
            if score >= 0.18:
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored:
            # A close second/third result is useful corroboration; unrelated
            # low-scoring documents are excluded instead of filling top-k.
            cutoff = max(0.18, scored[0][0] * 0.62)
            scored = [pair for pair in scored if pair[0] >= cutoff]
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
        return RetrievalResponse(
            evidence,
            status,
            message,
            best_score=round(scored[0][0], 4) if scored else 0.0,
            candidate_count=len(scored),
        )

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
    def _terms(text: str) -> set[str]:
        stop_words = {
            'a', 'an', 'and', 'are', 'after', 'before', 'for', 'from', 'in',
            'into', 'is', 'of', 'on', 'or', 'the', 'to', 'use', 'when', 'with',
        }
        terms: set[str] = set()
        for token in re.findall(r'[a-z0-9]+', text.lower()):
            if token in stop_words or len(token) < 3:
                continue
            if token.endswith('ies') and len(token) > 4:
                token = f'{token[:-3]}y'
            elif token.endswith('s') and len(token) > 4:
                token = token[:-1]
            terms.add(token)
        return terms

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        left_norm = sum(value * value for value in left) ** 0.5
        right_norm = sum(value * value for value in right) ** 0.5
        if not left_norm or not right_norm:
            return 0.0
        return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)))
