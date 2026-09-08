'''Bounded FIFO analysis queue with safe public status projections.

The queue is deliberately in-memory for the single-instance deployment
profile.  It enforces one active workflow and a small bounded waiting list;
the public API never exposes the incident text from a job record.
'''

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.graph.workflow import WorkflowOutput
from app.models.schemas import Incident

TERMINAL_STATUSES = {'complete', 'failed', 'cancelled'}


class QueueFullError(Exception):
    '''Raised when the active slot and bounded waiting list are full.'''


class JobNotFoundError(Exception):
    '''Raised when a job identifier is unknown.'''


@dataclass
class JobRecord:
    job_id: UUID
    client_request_id: UUID | None
    description: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = 'queued'
    result: WorkflowOutput | None = None
    error_code: str | None = None
    cancel_requested: bool = False
    stream_owner: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class InMemoryJobStore:
    def __init__(self, queue_capacity: int = 1):
        self.queue_capacity = queue_capacity
        self._jobs: dict[UUID, JobRecord] = {}
        self._order: deque[UUID] = deque()
        self._lock = asyncio.Lock()

    async def create(self, description: str, client_request_id: UUID | None) -> tuple[JobRecord, bool]:
        async with self._lock:
            if client_request_id is not None:
                for existing in self._jobs.values():
                    if existing.client_request_id == client_request_id:
                        return existing, False
            load = sum(item.status in {'queued', 'running'} for item in self._jobs.values())
            if load >= 1 + self.queue_capacity:
                raise QueueFullError
            record = JobRecord(uuid4(), client_request_id, description)
            self._jobs[record.job_id] = record
            self._order.append(record.job_id)
            self._emit_locked(record, 'queued', position=load)
            return record, True

    async def get(self, job_id: UUID) -> JobRecord:
        async with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise JobNotFoundError from exc

    async def claim_next(self) -> JobRecord | None:
        async with self._lock:
            if any(item.status == 'running' for item in self._jobs.values()):
                return None
            while self._order:
                record = self._jobs[self._order.popleft()]
                if record.status != 'queued':
                    continue
                self._emit_locked(record, 'running', position=0)
                return record
            return None

    async def set_complete(self, record: JobRecord, output: WorkflowOutput) -> None:
        async with self._lock:
            record.result = output
            self._emit_locked(record, 'complete', position=0)

    async def set_failed(self, record: JobRecord, code: str = 'INVESTIGATION_FAILED') -> None:
        async with self._lock:
            record.error_code = code
            self._emit_locked(record, 'failed', position=0)

    async def cancel(self, job_id: UUID) -> JobRecord:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise JobNotFoundError
            if record.status == 'queued':
                self._emit_locked(record, 'cancelled', position=0)
            elif record.status == 'running':
                record.cancel_requested = True
                self._emit_locked(record, 'cancelled', position=0)
            return record

    async def position(self, record: JobRecord) -> int | None:
        async with self._lock:
            if record.status == 'running':
                return 0
            if record.status != 'queued':
                return None
            return sum(
                item.status == 'queued' and item.created_at <= record.created_at
                for item in self._jobs.values()
            )

    def _emit_locked(self, record: JobRecord, status: str, *, position: int) -> None:
        record.status = status
        event: dict[str, Any] = {
            'job_id': str(record.job_id),
            'status': status,
            'position': position,
        }
        if status == 'complete' and record.result is not None:
            event['result'] = record.result
        if status == 'failed':
            event['error'] = {'code': record.error_code or 'INVESTIGATION_FAILED'}
        record.events.append(event)
        record.changed.set()


class AnalysisCoordinator:
    '''Serializes workflow execution and exposes queue-safe lifecycle events.'''

    def __init__(self, workflow: Any, *, queue_capacity: int = 1, timeout_seconds: float = 120):
        self.workflow = workflow
        self.timeout_seconds = timeout_seconds
        self.store = InMemoryJobStore(queue_capacity)
        self._drain_task: asyncio.Task[None] | None = None
        self._running_tasks: dict[UUID, asyncio.Task[None]] = {}

    async def submit(self, description: str, client_request_id: UUID | None = None) -> tuple[JobRecord, bool]:
        record, created = await self.store.create(description, client_request_id)
        if created:
            self._ensure_drain()
        return record, created

    async def wait(self, record: JobRecord) -> WorkflowOutput:
        while record.status not in TERMINAL_STATUSES:
            await record.changed.wait()
            record.changed.clear()
        if record.status == 'complete' and record.result is not None:
            return record.result
        if record.status == 'cancelled':
            raise RuntimeError('CANCELLED')
        raise RuntimeError(record.error_code or 'INVESTIGATION_FAILED')

    async def cancel(self, job_id: UUID) -> JobRecord:
        record = await self.store.cancel(job_id)
        task = self._running_tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        return record

    async def public_status(self, record: JobRecord) -> dict[str, Any]:
        position = await self.store.position(record)
        payload: dict[str, Any] = {
            'job_id': str(record.job_id),
            'status': record.status,
            'position': position,
            'events_url': f'/api/incidents/analyze/jobs/{record.job_id}/events',
            'status_url': f'/api/incidents/analyze/jobs/{record.job_id}',
        }
        if record.status == 'complete' and record.result is not None:
            payload['result'] = record.result
        if record.status == 'failed':
            payload['error'] = {'code': record.error_code or 'INVESTIGATION_FAILED'}
        return payload

    async def events(self, record: JobRecord):
        if record.stream_owner:
            raise RuntimeError('event stream already in use')
        record.stream_owner = True
        index = 0
        try:
            while True:
                while index < len(record.events):
                    event = record.events[index]
                    index += 1
                    if isinstance(event.get('result'), WorkflowOutput):
                        event = {
                            **event,
                            'result': event['result'],
                        }
                    yield event
                if record.status in TERMINAL_STATUSES:
                    break
                record.changed.clear()
                if index < len(record.events):
                    continue
                try:
                    await asyncio.wait_for(record.changed.wait(), timeout=30)
                except TimeoutError:
                    yield {'job_id': str(record.job_id), 'status': record.status, 'position': await self.store.position(record)}
        finally:
            record.stream_owner = False

    def _ensure_drain(self) -> None:
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            record = await self.store.claim_next()
            if record is None:
                return
            task = asyncio.create_task(self._process(record))
            self._running_tasks[record.job_id] = task
            try:
                await task
            finally:
                self._running_tasks.pop(record.job_id, None)

    async def _process(self, record: JobRecord) -> None:
        try:
            output = await asyncio.wait_for(
                self.workflow.analyze(Incident(description=record.description)),
                timeout=self.timeout_seconds,
            )
            if record.cancel_requested:
                await self.store.cancel(record.job_id)
            else:
                await self.store.set_complete(record, output)
        except asyncio.CancelledError:
            await self.store.cancel(record.job_id)
        except TimeoutError:
            await self.store.set_failed(record, 'WORKFLOW_TIMEOUT')
        except Exception:  # noqa: BLE001
            await self.store.set_failed(record)
