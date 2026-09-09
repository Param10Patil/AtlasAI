"""Typed domain and public-boundary schemas.

These models are intentionally small.  Internal implementations may keep more
metadata, but public models must be constructed as an allowlist projection.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.kubernetes.contracts import ClusterObservation


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    description: str = Field(min_length=1, max_length=4000)
    service: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    severity: Severity | None = None
    source: str = Field(default="manual", max_length=40)
    observation: ClusterObservation | None = None
    remediation_requested: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("description")
    @classmethod
    def trim_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("description must contain non-whitespace text")
        return value


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=800)
    score: float | None = Field(default=None, ge=0, le=1)


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(default=1, ge=1, le=5)
    text: str = Field(min_length=1, max_length=500)
    requires_confirmation: bool = True
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(default_factory=uuid4)
    status: str = "complete"
    severity: Severity
    title: str = Field(min_length=1, max_length=160)
    likely_causes: list[str] = Field(default_factory=list, max_length=5)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list, max_length=5)
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=5)
    limitations: list[str] = Field(default_factory=list, max_length=5)
