"""Generic health-policy detection from real Kubernetes observations."""

from app.kubernetes.contracts import ClusterObservation, HealthStatus, IncidentEvent


class IncidentDetector:
    """Turn an objective health violation into one bounded incident event."""

    def detect(self, observation: ClusterObservation) -> IncidentEvent | None:
        if observation.connection.value != "connected" or observation.health.status is not HealthStatus.DEGRADED:
            return None
        description = (
            f"Kubernetes detected a health-policy violation for "
            f"{observation.namespace}/{observation.workload}: {observation.signal_text}"
        )
        return IncidentEvent(
            description=description[:4000],
            namespace=observation.namespace,
            workload=observation.workload,
            observation_ids=[observation.observation_id],
            observation=observation,
        )
