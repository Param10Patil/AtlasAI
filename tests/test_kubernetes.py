from types import SimpleNamespace

import pytest

from app.kubernetes.contracts import (
    ClusterObservation,
    ConnectionStatus,
    DeploymentObservation,
    HealthAssessment,
    HealthStatus,
    PodObservation,
)
from app.kubernetes.detection import IncidentDetector
from app.kubernetes.service import KubernetesService, KubernetesUnavailable


def _observation(status: HealthStatus) -> ClusterObservation:
    return ClusterObservation(
        connection=ConnectionStatus.CONNECTED,
        namespace='ops-demo',
        workload='checkout-api',
        deployment=DeploymentObservation(
            name='checkout-api', namespace='ops-demo', desired_replicas=1,
            available_replicas=0 if status is HealthStatus.DEGRADED else 1,
            ready_replicas=0 if status is HealthStatus.DEGRADED else 1,
            updated_replicas=1, rollout_complete=status is HealthStatus.HEALTHY,
        ),
        pods=[PodObservation(name='checkout-api-1', phase='Pending', ready=status is HealthStatus.HEALTHY)],
        health=HealthAssessment(status=status, reasons=['available replicas 0/1'] if status is HealthStatus.DEGRADED else []),
    )


def test_detector_emits_event_only_for_connected_degraded_observation():
    event = IncidentDetector().detect(_observation(HealthStatus.DEGRADED))
    assert event is not None
    assert event.source == 'kubernetes'
    assert event.observation_ids == [event.observation.observation_id]
    assert 'available replicas 0/1' in event.description
    assert IncidentDetector().detect(_observation(HealthStatus.HEALTHY)) is None


@pytest.mark.asyncio
async def test_kubernetes_scope_rejects_non_demo_targets():
    service = KubernetesService(mode='disabled')
    with pytest.raises(KubernetesUnavailable):
        await service.observe('default', 'checkout-api')
    with pytest.raises(KubernetesUnavailable):
        await service.observe('ops-demo', 'other-workload')


def test_observation_signal_is_bounded_and_contains_real_fields():
    observation = _observation(HealthStatus.DEGRADED)
    observation.pods[0].waiting_reason = 'ImagePullBackOff'
    signal = observation.signal_text
    assert 'desired replicas 1' in signal
    assert 'ImagePullBackOff' in signal
    assert len(signal) <= 1800


def test_health_parser_handles_kubernetes_like_objects():
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(name='checkout-api', annotations={'deployment.kubernetes.io/revision': '2'}),
        spec=SimpleNamespace(
            replicas=2,
            template=SimpleNamespace(spec=SimpleNamespace(containers=[SimpleNamespace(image='nginx:1.25')])),
        ),
        status=SimpleNamespace(available_replicas=1, ready_replicas=1, updated_replicas=2),
    )
    parsed = KubernetesService._deployment(deployment, 'ops-demo')
    assert parsed.desired_replicas == 2
    assert parsed.available_replicas == 1
    assert parsed.revision == '2'
    assert parsed.rollout_complete is False
