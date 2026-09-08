"""Safe handoff models between resolution and the public response."""

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import AnalysisResult


class GuardrailContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_resolution: AnalysisResult
    allowed_evidence_ids: tuple[str, ...] = ()
    safety_policy_version: str = Field(default="2026-01", max_length=40)


class GuardrailDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    result: AnalysisResult | None = None
    limitation: str | None = Field(default=None, max_length=300)
