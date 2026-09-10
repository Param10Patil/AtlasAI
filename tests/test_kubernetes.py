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
from app.remediation.policy import ActionPolicy, PolicyViolation


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


@pytest.mark.asyncio
async def test_kubernetes_scope_rejects_non_checkout_workload_even_if_service_is_constructed_directly():
    service = KubernetesService(mode='disabled', namespace='ops-demo', workload='other-workload')
    with pytest.raises(KubernetesUnavailable):
        await service.observe('ops-demo', 'other-workload')
    with pytest.raises(KubernetesUnavailable):
        await service.summary('other-namespace')


def test_observation_signal_is_bounded_and_contains_real_fields():
    observation = _observation(HealthStatus.DEGRADED)
    observation.pods[0].waiting_reason = 'ImagePullBackOff'
    signal = observation.signal_text
    assert 'desired replicas 1' in signal
    assert 'ImagePullBackOff' in signal
    assert len(signal) <= 1800


def test_service_endpoint_read_is_distinct_from_control_plane_connection():
    class Core:
        def read_namespaced_endpoints(self, name, namespace, **kwargs):
            return SimpleNamespace(subsets=[SimpleNamespace(addresses=[])])

    service = KubernetesService(mode='execute')
    service._core = Core()
    service._api_exception = Exception
    assert service._service_available('ops-demo', 'checkout-api') is False
    assessed = KubernetesService._assess(_observation(HealthStatus.HEALTHY).deployment, [], False)
    assert assessed.status is HealthStatus.DEGRADED
    assert 'service has no ready endpoints' in assessed.reasons


def test_controlled_injection_removes_only_pods_present_before_the_fault():
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(annotations={}),
        spec=SimpleNamespace(template=SimpleNamespace(spec=SimpleNamespace(
            containers=[SimpleNamespace(image='nginx:1.25-alpine', command=None, readiness_probe=None)],
        ))),
    )
    old_pod = SimpleNamespace(metadata=SimpleNamespace(name='checkout-api-old'))

    class Apps:
        def __init__(self):
            self.patch = None

        def read_namespaced_deployment(self, name, namespace, **kwargs):
            return deployment

        def patch_namespaced_deployment(self, name, namespace, patch, **kwargs):
            self.patch = patch

    class Core:
        def __init__(self):
            self.deleted = []

        def list_namespaced_pod(self, namespace, label_selector, **kwargs):
            return SimpleNamespace(items=[old_pod])

        def delete_namespaced_pod(self, name, namespace, **kwargs):
            self.deleted.append(name)

    service = KubernetesService(mode='execute')
    service._loaded = True
    service._load_error = None
    service._apps = Apps()
    service._core = Core()
    service._api_exception = Exception
    expected = _observation(HealthStatus.DEGRADED)
    service._observe_sync = lambda namespace, workload: expected
    observed = service._inject_sync('deployment_failure')
    assert observed is expected
    assert service._core.deleted == ['checkout-api-old']
    assert service._apps.patch['spec']['template']['spec']['containers'][0]['image'] == 'nginx:opspilot-image-does-not-exist'


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


def test_action_policy_rejects_arbitrary_target_and_action():
    policy = ActionPolicy()
    assert policy.validate_target('ops-demo/checkout-api') == ('ops-demo', 'checkout-api')
    with pytest.raises(PolicyViolation):
        policy.validate_target('default/checkout-api')
    with pytest.raises(PolicyViolation):
        policy.validate_target('ops-demo/other')
    with pytest.raises(PolicyViolation):
        policy.validate_action('delete_namespace')


def test_rollback_patch_removes_injected_command_when_healthy_revision_had_none():
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(annotations={
            'opspilot.io/healthy-image': 'nginx:1.25-alpine',
            'opspilot.io/healthy-command': 'null',
            'opspilot.io/healthy-readiness': 'null',
        }),
    )

    class Apps:
        def __init__(self):
            self.patch = None

        def read_namespaced_deployment(self, name, namespace, **kwargs):
            return deployment

        def patch_namespaced_deployment(self, name, namespace, patch, **kwargs):
            self.patch = patch

    service = KubernetesService(mode='execute')
    service._loaded = True
    service._load_error = None
    service._apps = Apps()
    service._core = object()
    service._rollback('checkout-api', 'ops-demo')
    container = service._apps.patch['spec']['template']['spec']['containers'][0]
    assert container['image'] == 'nginx:1.25-alpine'
    assert container['command'] is None
    assert container['readinessProbe'] is None


def test_clear_temporary_condition_restores_recorded_template_via_api_patch():
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(annotations={
            'opspilot.io/healthy-image': 'nginx:1.25-alpine',
            'opspilot.io/healthy-command': 'null',
            'opspilot.io/healthy-readiness': 'null',
        }),
    )

    class Apps:
        def __init__(self):
            self.patch = None

        def read_namespaced_deployment(self, name, namespace, **kwargs):
            return deployment

        def patch_namespaced_deployment(self, name, namespace, patch, **kwargs):
            self.patch = patch

    service = KubernetesService(mode='execute')
    service._loaded = True
    service._load_error = None
    service._apps = Apps()
    service._core = object()
    service._clear_temporary_condition('checkout-api', 'ops-demo')
    container = service._apps.patch['spec']['template']['spec']['containers'][0]
    assert container == {'name': 'checkout-api', 'image': 'nginx:1.25-alpine', 'command': None, 'readinessProbe': None}
