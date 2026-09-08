'''Strict contracts for policy-gated remediation.'''

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SafeAction(str, Enum):
    RESTART_POD = 'restart_pod'
    SCALE_DEPLOYMENT = 'scale_deployment'
    ROLLBACK_DEPLOYMENT = 'rollback_deployment'
    CLEAR_TEMPORARY_CONDITION = 'clear_temporary_condition'


class RemediationContext(BaseModel):
    model_config = ConfigDict(extra='forbid')

    incident_summary: str = Field(min_length=1, max_length=500)
    service: str | None = Field(default=None, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    probable_causes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    safety_constraints: tuple[str, ...] = ('Allow only the four safe actions', 'Verify health after execution')


class RemediationResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    status: str = Field(pattern='^(disabled|awaiting_approval|executed|failed|verified)$')
    action: SafeAction | None = None
    target: str | None = Field(default=None, max_length=120)
    message: str = Field(min_length=1, max_length=300)
    health_verified: bool = False
    retry_recommended: bool = False
