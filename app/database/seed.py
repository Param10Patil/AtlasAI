'''Load the checked-in demonstration data for local or deterministic runs.'''

from pathlib import Path

from app.database.repository import InMemoryRepository, PostgresRepository, seed_records
from app.providers.embeddings import HashEmbeddingProvider
from app.rag.service import RAGService


async def build_seed_repository(knowledge_dir: Path | None = None) -> InMemoryRepository:
    directory = knowledge_dir or Path(__file__).resolve().parents[2] / 'knowledge'
    records, history = seed_records(directory)
    repository = InMemoryRepository(history=history)
    rag = RAGService(repository, HashEmbeddingProvider())
    await rag.ingest_records(records)
    return repository


async def seed_postgres(repository: PostgresRepository, knowledge_dir: Path | None = None) -> int:
    directory = knowledge_dir or Path(__file__).resolve().parents[2] / 'knowledge'
    records, history = seed_records(directory)
    rag = RAGService(repository, HashEmbeddingProvider())
    added = await rag.ingest_records(records)
    await repository.upsert_history(history)
    return added
