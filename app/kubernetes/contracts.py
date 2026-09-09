"""Small typed contracts for Kubernetes observations and incidents.

These models deliberately contain only operational facts that can be obtained
from the Kubernetes API.  They are also safe to pass through the agent
projectors without exposing a full Kubernetes object or API payload.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class PodObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    phase: str = Field(default="Unknown", max_length=40)
    ready: bool = False
    restarts: int = Field(default=0, ge=0)
    waiting_reason: str | None = Field(default=None, max_length=80)
    termination_reason: str | None = Field(default=None, max_length=80)


class DeploymentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    namespace: str = Field(min_length=1, max_length=63)
    desired_replicas: int = Field(default=0, ge=0)
    available_replicas: int = Field(default=0, ge=0)
    ready_replicas: int = Field(default=0, ge=0)
    updated_replicas: int = Field(default=0, ge=0)
    revision: str | None = Field(default=None, max_length=40)
    image: str | None = Field(default=None, max_length=300)
    rollout_complete: bool = False


class EventObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=120)
    event_type: str | None = Field(default=None, max_length=40)
    message: str = Field(default="", max_length=800)
    involved_object: str | None = Field(default=None, max_length=120)
    observed_at: datetime | None = None


class HealthAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: HealthStatus
    reasons: list[str] = Field(default_factory=list, max_length=8)


class ClusterObservation(BaseModel):
    """Bounded snapshot of one approved workload and its recent events."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(default_factory=lambda: f"obs-{uuid4().hex}", max_length=80)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(default="kubernetes", max_length=40)
    connection: ConnectionStatus
    namespace: str = Field(min_length=1, max_length=63)
    workload: str = Field(min_length=1, max_length=120)
    deployment: DeploymentObservation | None = None
    pods: list[PodObservation] = Field(default_factory=list, max_length=20)
    events: list[EventObservation] = Field(default_factory=list, max_length=20)
    health: HealthAssessment
    limitations: list[str] = Field(default_factory=list, max_length=5)

    @property
    def signal_text(self) -> str:
        """A compact, non-sensitive signal string for the triage projector."""

        deployment = self.deployment
        facts: list[str] = [f"namespace {self.namespace}", f"workload {self.workload}"]
        if deployment:
            facts.extend([
                f"desired replicas {deployment.desired_replicas}",
                f"available replicas {deployment.available_replicas}",
                f"ready replicas {deployment.ready_replicas}",
                f"updated replicas {deployment.updated_replicas}",
                f"rollout {'complete' if deployment.rollout_complete else 'incomplete'}",
            ])
        for pod in self.pods[:5]:
            state = pod.waiting_reason or pod.termination_reason or pod.phase
            facts.append(f"pod {pod.name} {state} {'ready' if pod.ready else 'not ready'} restarts {pod.restarts}")
        for event in self.events[:5]:
            facts.append(f"event {event.reason or 'unknown'} {event.message}".strip())
        facts.extend(self.health.reasons[:4])
        return "; ".join(facts)[:1800]


class IncidentEvent(BaseModel):
    """An incident emitted by a health-policy violation, not a scenario name."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(default_factory=lambda: f"INC-{uuid4().hex[:12].upper()}", max_length=40)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(default="kubernetes", max_length=40)
    description: str = Field(min_length=1, max_length=4000)
    namespace: str = Field(min_length=1, max_length=63)
    workload: str = Field(min_length=1, max_length=120)
    observation_ids: list[str] = Field(default_factory=list, max_length=5)
    observation: ClusterObservation


class NamespaceSummary(BaseModel):
    """Real, bounded namespace overview for the control center."""

    model_config = ConfigDict(extra="forbid")

    connection: ConnectionStatus
    namespace: str = Field(min_length=1, max_length=63)
    deployments_total: int = Field(default=0, ge=0)
    deployments_available: int = Field(default=0, ge=0)
    pods_total: int = Field(default=0, ge=0)
    pods_ready: int = Field(default=0, ge=0)
    observations: list[DeploymentObservation] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=5)
