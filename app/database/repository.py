'''Repository interfaces and small deterministic/infrastructure adapters.'''

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, Sequence
from uuid import UUID, uuid4

from app.models.schemas import AnalysisResult, Incident


@dataclass(frozen=True)
class KnowledgeRecord:
    id: str
    document_id: str
    title: str
    content: str
    source: str
    metadata: dict[str, str] = field(default_factory=dict)
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class HistoricalRecord:
    id: str
    service: str | None
    category: str
    summary: str
    resolution: str
    created_at: datetime


class RepositoryError(RuntimeError):
    '''A safe infrastructure error suitable for mapping to a public status.'''


class VectorExtensionUnavailable(RepositoryError):
    '''PostgreSQL is reachable but pgvector is not installed or enabled.'''


class Repository(Protocol):
    async def health(self) -> tuple[bool, str]: ...
    async def save_incident(self, incident: Incident) -> None: ...
    async def save_result(self, result: AnalysisResult) -> None: ...
    async def list_knowledge(self) -> list[KnowledgeRecord]: ...
    async def list_history(self, service: str | None, category: str, limit: int) -> list[HistoricalRecord]: ...
    async def similarity_search(self, vector: Sequence[float], limit: int) -> list[KnowledgeRecord]: ...


class InMemoryRepository:
    '''Deterministic repository used for development and credential-free tests.'''

    def __init__(self, knowledge: list[KnowledgeRecord] | None = None, history: list[HistoricalRecord] | None = None):
        self.knowledge = knowledge or []
        self.history = history or []
        self.incidents: list[Incident] = []
        self.results: list[AnalysisResult] = []

    async def health(self) -> tuple[bool, str]:
        return True, 'memory repository ready'

    async def save_incident(self, incident: Incident) -> None:
        self.incidents.append(incident)

    async def save_result(self, result: AnalysisResult) -> None:
        self.results.append(result)

    async def list_knowledge(self) -> list[KnowledgeRecord]:
        return list(self.knowledge)

    async def list_history(self, service: str | None, category: str, limit: int) -> list[HistoricalRecord]:
        rows = [item for item in self.history if item.category == category]
        if service:
            rows = [item for item in rows if item.service in {service, None}]
        return rows[:limit]

    async def similarity_search(self, vector: Sequence[float], limit: int) -> list[KnowledgeRecord]:
        def score(record: KnowledgeRecord) -> float:
            if not record.embedding or len(record.embedding) != len(vector):
                return 0.0
            dot = sum(a * b for a, b in zip(record.embedding, vector))
            left = sum(a * a for a in record.embedding) ** 0.5
            right = sum(b * b for b in vector) ** 0.5
            return dot / (left * right) if left and right else 0.0

        return sorted(self.knowledge, key=score, reverse=True)[:limit]


class PostgresRepository:
    '''Small psycopg adapter. Connections are created lazily and never at import.'''

    def __init__(self, database_url: str, schema_path: Path | None = None):
        self.database_url = database_url
        self.schema_path = schema_path or Path(__file__).with_name('schema.sql')
        self._connection: Any = None

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise RepositoryError('psycopg is not installed; install runtime dependencies') from exc
        if self._connection is None or self._connection.closed:
            self._connection = psycopg.connect(self.database_url, connect_timeout=5)
        return self._connection

    def _initialize_sync(self) -> None:
        connection = self._connect()
        with connection.cursor() as cursor:
            cursor.execute(self.schema_path.read_text(encoding='utf-8'))
            cursor.execute('select exists (select 1 from pg_extension where extname = %s)', ('vector',))
            available = bool(cursor.fetchone()[0])
        connection.commit()
        if not available:
            raise VectorExtensionUnavailable('pgvector extension is unavailable; install pgvector and enable vector')

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _health_sync(self) -> tuple[bool, str]:
        try:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute('select 1')
                cursor.fetchone()
                cursor.execute('select exists (select 1 from pg_extension where extname = %s)', ('vector',))
                if not cursor.fetchone()[0]:
                    return False, 'pgvector extension is unavailable'
            return True, 'postgres repository ready'
        except VectorExtensionUnavailable:
            return False, 'pgvector extension is unavailable'
        except Exception:
            return False, 'database unavailable'

    async def health(self) -> tuple[bool, str]:
        return await asyncio.to_thread(self._health_sync)

    async def save_incident(self, incident: Incident) -> None:
        def write() -> None:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    'insert into incidents (id, description, service, category, severity, created_at) '
                    'values (%s, %s, %s, %s, %s, %s) on conflict (id) do nothing',
                    (incident.id, incident.description, incident.service, incident.category,
                     incident.severity.value if incident.severity else None, incident.created_at),
                )
            connection.commit()
        await asyncio.to_thread(write)

    async def save_result(self, result: AnalysisResult) -> None:
        payload = result.model_dump(mode='json')
        def write() -> None:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    'insert into analysis_results (id, request_id, payload, created_at) values (%s, %s, %s, %s) '
                    'on conflict (request_id) do update set payload = excluded.payload',
                    (uuid4(), result.request_id, json.dumps(payload), datetime.now(timezone.utc)),
                )
            connection.commit()
        await asyncio.to_thread(write)

    async def list_knowledge(self) -> list[KnowledgeRecord]:
        def read() -> list[KnowledgeRecord]:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute('select id, id, title, content, source, metadata, embedding from knowledge_documents')
                return [
                    KnowledgeRecord(str(row[0]), str(row[1]), row[2], row[3], row[4], row[5] or {}, tuple(row[6]) if row[6] else None)
                    for row in cursor.fetchall()
                ]
        return await asyncio.to_thread(read)

    async def list_history(self, service: str | None, category: str, limit: int) -> list[HistoricalRecord]:
        def read() -> list[HistoricalRecord]:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    'select id, service, category, summary, resolution, created_at from historical_incidents '
                    'where category = %s and (%s is null or service = %s or service is null) '
                    'order by created_at desc limit %s',
                    (category, service, service, limit),
                )
                return [HistoricalRecord(str(row[0]), row[1], row[2], row[3], row[4], row[5]) for row in cursor.fetchall()]
        return await asyncio.to_thread(read)

    async def similarity_search(self, vector: Sequence[float], limit: int) -> list[KnowledgeRecord]:
        values = list(vector)
        def read() -> list[KnowledgeRecord]:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    'select id, document_id, title, content, source, metadata, embedding '
                    'from knowledge_chunks where embedding is not null '
                    'order by embedding <=> %s::vector limit %s',
                    (str(values), limit),
                )
                return [KnowledgeRecord(str(row[0]), str(row[1]), row[2], row[3], row[4], row[5] or {}, tuple(row[6]) if row[6] else None) for row in cursor.fetchall()]
        return await asyncio.to_thread(read)


def seed_records(knowledge_dir: Path) -> tuple[list[KnowledgeRecord], list[HistoricalRecord]]:
    '''Load small checked-in seed files without writing to a database.'''

    records: list[KnowledgeRecord] = []
    for path in sorted(knowledge_dir.glob('*.md')):
        content = path.read_text(encoding='utf-8').strip()
        title = content.splitlines()[0].lstrip('# ').strip() or path.stem
        checksum = hashlib.sha256(content.encode('utf-8')).hexdigest()
        records.append(KnowledgeRecord(checksum[:16], checksum[:16], title, content, path.as_posix(), {'checksum': checksum}))
    history = [
        HistoricalRecord('history-payment-503', 'payments', 'availability_issue', 'Payment API returned 503 after a release', 'Paused rollout and restored the last known-good revision after approval', datetime(2025, 6, 12, tzinfo=timezone.utc)),
        HistoricalRecord('history-pool', 'orders', 'database_failure', 'Connection pool exhausted during a traffic spike', 'Removed a leaked transaction and reduced retry amplification', datetime(2025, 4, 3, tzinfo=timezone.utc)),
        HistoricalRecord('history-auth', 'identity', 'authentication_failure', 'Token validation failed after key rotation', 'Corrected the audience configuration and rotated keys through the normal process', datetime(2025, 2, 18, tzinfo=timezone.utc)),
    ]
    return records, history
