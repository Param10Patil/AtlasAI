"""Three-agent boundaries.

Implementations are added later; protocols make service seams explicit now.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import AnalysisResult, EvidenceItem, Incident, Severity


class TriageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_description: str = Field(min_length=1, max_length=4000)


class TriageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_summary: str = Field(min_length=1, max_length=500)
    service: str | None = Field(default=None, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    severity: Severity
    symptoms: list[str] = Field(default_factory=list, max_length=8)
    likely_causes: list[str] = Field(default_factory=list, max_length=5)
    search_terms: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    classifier_source: str = "rule_fallback"
    limitations: list[str] = Field(default_factory=list, max_length=5)


class KnowledgeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_summary: str = Field(min_length=1, max_length=500)
    service: str | None = Field(default=None, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    symptoms: list[str] = Field(default_factory=list, max_length=8)
    search_terms: list[str] = Field(default_factory=list, max_length=10)


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runbook_evidence: list[EvidenceItem] = Field(default_factory=list, max_length=5)
    historical_incidents: list[dict[str, str]] = Field(default_factory=list, max_length=5)
    retrieval_limitations: list[str] = Field(default_factory=list, max_length=5)


class ResolutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_summary: str = Field(min_length=1, max_length=500)
    triage: TriageResult
    evidence: EvidenceBundle
    safety_constraints: tuple[str, ...] = ("Never execute commands", "Require operator confirmation")


class TriagePort(Protocol):
    async def triage(self, context: TriageContext) -> TriageResult: ...


class KnowledgePort(Protocol):
    async def investigate(self, context: KnowledgeContext) -> EvidenceBundle: ...


class ResolutionPort(Protocol):
    async def resolve(self, context: ResolutionContext) -> AnalysisResult: ...


class IncidentWorkflow(Protocol):
    async def analyze(self, incident: Incident) -> AnalysisResult: ...
