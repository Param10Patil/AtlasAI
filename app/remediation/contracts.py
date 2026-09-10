'''Strict contracts for policy-gated remediation.'''

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.kubernetes.contracts import ClusterObservation
from app.models.schemas import EvidenceItem, RecommendedAction


class SafeAction(str, Enum):
    RESTART_POD = 'restart_pod'
    SCALE_DEPLOYMENT = 'scale_deployment'
    ROLLBACK_DEPLOYMENT = 'rollback_deployment'
    CLEAR_TEMPORARY_CONDITION = 'clear_temporary_condition'


class ExecutionPreview(BaseModel):
    """Truthful description of the operation the configured executor will call."""

    model_config = ConfigDict(extra='forbid')

    action: SafeAction
    category: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=120)
    namespace: str = Field(min_length=1, max_length=63)
    resource_type: str = Field(min_length=1, max_length=120)
    why: str = Field(min_length=1, max_length=240)
    operation: str = Field(min_length=1, max_length=400)
    execution_method: str = Field(min_length=1, max_length=160)
    api_method: str = Field(min_length=1, max_length=240)
    rag_evidence: list[EvidenceItem] = Field(default_factory=list, max_length=3)
    rag_commands: list[str] = Field(default_factory=list, max_length=3)
    policy_result: str = Field(min_length=1, max_length=240)
    approval_required: bool = False


class RemediationContext(BaseModel):
    model_config = ConfigDict(extra='forbid')

    incident_summary: str = Field(min_length=1, max_length=500)
    service: str | None = Field(default=None, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    probable_causes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    recommended_actions: tuple[RecommendedAction, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    observation: ClusterObservation | None = None
    safety_constraints: tuple[str, ...] = ('Allow only the four safe actions', 'Verify health after execution')


class RemediationResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    status: str = Field(pattern='^(disabled|awaiting_approval|executed|failed|verified)$')
    action: SafeAction | None = None
    target: str | None = Field(default=None, max_length=120)
    message: str = Field(min_length=1, max_length=300)
    health_verified: bool = False
    retry_recommended: bool = False
    execution_preview: ExecutionPreview | None = None
    before_observation: ClusterObservation | None = None
    after_observation: ClusterObservation | None = None


class RemediationAudit(BaseModel):
    model_config = ConfigDict(extra='forbid')

    incident_id: str = Field(min_length=1, max_length=80)
    actor: str = Field(pattern='^(ai|user)$')
    action: SafeAction
    target: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = ()
    before_observation: ClusterObservation | None = None
    after_observation: ClusterObservation | None = None
    verification_result: str = Field(min_length=1, max_length=80)
    created_at: str = Field(min_length=1, max_length=80)
