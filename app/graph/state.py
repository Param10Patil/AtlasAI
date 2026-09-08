'''Serializable workflow state declaration.

Nodes return partial updates and projectors keep each agent input smaller than
this orchestration state.
'''

from typing import Any, TypedDict
from uuid import UUID

from app.agents.ports import EvidenceBundle, TriageResult
from app.guardrails.contracts import GuardrailDecision
from app.models.schemas import AnalysisResult
from app.remediation.contracts import RemediationResult


class WorkflowState(TypedDict, total=False):
    request_id: UUID
    original_incident: str
    triage_result: TriageResult
    evidence: EvidenceBundle
    resolution: AnalysisResult
    guardrail_decision: GuardrailDecision
    remediation: RemediationResult
    errors: list[str]
    degraded: bool
    metadata: dict[str, Any]
