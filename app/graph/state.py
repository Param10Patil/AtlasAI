"""Serializable workflow state declaration.

Nodes will return partial updates; projectors in the graph keep each agent's
input smaller than this orchestration state.
"""

from typing import TypedDict
from uuid import UUID

from app.agents.ports import EvidenceBundle, TriageResult
from app.models.schemas import AnalysisResult


class WorkflowState(TypedDict, total=False):
    request_id: UUID
    original_incident: str
    triage_result: TriageResult
    evidence: EvidenceBundle
    resolution: AnalysisResult
    errors: list[str]
    degraded: bool
