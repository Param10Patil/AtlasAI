"""Bounded Kubernetes observation, injection, and remediation operations.

The client is lazy and optional: the API can still run in Cloud Run or local
memory mode when no kubeconfig is present.  When enabled, every operation is
limited to the configured demo namespace and workload; there is no shell or
arbitrary kubectl escape hatch.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Protocol

from app.kubernetes.contracts import (
    ClusterObservation,
    ConnectionStatus,
    DeploymentObservation,
    EventObservation,
    HealthAssessment,
    HealthStatus,
    NamespaceSummary,
    PodObservation,
)
from app.remediation.policy import ActionPolicy, PolicyViolation


class KubernetesUnavailable(RuntimeError):
    """The cluster could not be reached or is not configured."""


class KubernetesObserver(Protocol):
    async def observe(self, namespace: str, workload: str) -> ClusterObservation: ...

    async def summary(self, namespace: str) -> NamespaceSummary: ...


class KubernetesService:
    """One policy-scoped adapter for reads, demo injection, and safe actions."""

    _name_pattern = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    _allowed_scenarios: ClassVar[frozenset[str]] = frozenset({"deployment_failure", "pod_crash", "rollout_failure"})
    _allowed_actions: ClassVar[frozenset[str]] = frozenset({"restart_pod", "scale_deployment", "rollback_deployment", "clear_temporary_condition"})

    def __init__(self, *, mode: str = "disabled", namespace: str = "ops-demo", workload: str = "checkout-api"):
        self.mode = mode
        self.namespace = namespace
        self.workload = workload
        # The protected namespace is an invariant, not user-configurable
        # authorization. Settings also validate this, but keeping the fixed
        # value here makes direct service use fail closed as well.
        self.policy = ActionPolicy(allowed_namespace="ops-demo", allowed_targets=frozenset({"checkout-api"}))
        self._loaded = False
        self._load_error: str | None = None
        self._core: Any = None
        self._apps: Any = None
        self._api_exception: type[Exception] = Exception

    @property
    def configured(self) -> bool:
        return self.mode in {"observe", "execute"}

    @property
    def connection(self) -> ConnectionStatus:
        if not self.configured:
            return ConnectionStatus.OFFLINE
        self._ensure_client()
        return ConnectionStatus.CONNECTED if self._load_error is None else ConnectionStatus.OFFLINE

    def _ensure_client(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.configured:
            self._load_error = "Kubernetes integration is disabled"
            return
        try:
            from kubernetes import client, config
            from kubernetes.config.config_exception import ConfigException
        except ImportError:
            self._load_error = "Kubernetes client is not installed"
            return
        try:
            try:
                config.load_incluster_config()
            except ConfigException:
                config.load_kube_config()
            self._core = client.CoreV1Api()
            self._apps = client.AppsV1Api()
            self._api_exception = client.exceptions.ApiException
        except Exception as exc:  # noqa: BLE001 - connection detail stays private
            self._load_error = f"Kubernetes configuration unavailable ({type(exc).__name__})"

    def _scope(self, namespace: str, workload: str) -> None:
        if not self._name_pattern.fullmatch(workload):
            raise KubernetesUnavailable("workload is outside the approved scope")
        try:
            self.policy.validate_target(f"{namespace}/{workload}")
        except PolicyViolation as exc:
            raise KubernetesUnavailable(str(exc)) from exc

    def _offline(self, namespace: str, workload: str, reason: str) -> ClusterObservation:
        return ClusterObservation(
            connection=ConnectionStatus.OFFLINE,
            namespace=namespace,
            workload=workload,
            health=HealthAssessment(status=HealthStatus.UNKNOWN, reasons=[reason]),
            limitations=[reason],
        )

    async def observe(self, namespace: str, workload: str) -> ClusterObservation:
        self._scope(namespace, workload)
        return await asyncio.to_thread(self._observe_sync, namespace, workload)

    def _observe_sync(self, namespace: str, workload: str) -> ClusterObservation:
        self._ensure_client()
        if self._load_error or self._apps is None or self._core is None:
            return self._offline(namespace, workload, self._load_error or "Kubernetes client unavailable")
        try:
            deployment = self._apps.read_namespaced_deployment(workload, namespace)
            pods = self._core.list_namespaced_pod(namespace, label_selector=f"app.kubernetes.io/name={workload}").items
            events = self._core.list_namespaced_event(namespace).items
        except self._api_exception as exc:
            if getattr(exc, "status", None) == 404:
                return self._offline(namespace, workload, "approved demo workload was not found")
            return self._offline(namespace, workload, "Kubernetes observation request failed")
        except Exception:  # noqa: BLE001
            return self._offline(namespace, workload, "Kubernetes observation request failed")
        deployment_observation = self._deployment(deployment, namespace)
        pod_observations = [self._pod(pod) for pod in pods[:20]]
        event_observations = [self._event(event, workload) for event in events if self._event_matches(event, workload)][-20:]
        health = self._assess(deployment_observation, pod_observations)
        return ClusterObservation(
            connection=ConnectionStatus.CONNECTED,
            namespace=namespace,
            workload=workload,
            deployment=deployment_observation,
            pods=pod_observations,
            events=event_observations,
            health=health,
        )

    async def summary(self, namespace: str) -> NamespaceSummary:
        if namespace != self.policy.allowed_namespace:
            raise KubernetesUnavailable("namespace is outside the approved scope")
        return await asyncio.to_thread(self._summary_sync, namespace)

    def _summary_sync(self, namespace: str) -> NamespaceSummary:
        self._ensure_client()
        if self._load_error or self._apps is None or self._core is None:
            return NamespaceSummary(
                connection=ConnectionStatus.OFFLINE,
                namespace=namespace,
                limitations=[self._load_error or "Kubernetes client unavailable"],
            )
        try:
            deployments = self._apps.list_namespaced_deployment(namespace).items
            pods = self._core.list_namespaced_pod(namespace).items
        except Exception:  # noqa: BLE001
            return NamespaceSummary(connection=ConnectionStatus.OFFLINE, namespace=namespace, limitations=["Kubernetes summary request failed"])
        observations = [self._deployment(item, namespace) for item in deployments[:20]]
        return NamespaceSummary(
            connection=ConnectionStatus.CONNECTED,
            namespace=namespace,
            deployments_total=len(observations),
            deployments_available=sum(item.available_replicas >= item.desired_replicas > 0 for item in observations),
            pods_total=len(pods),
            pods_ready=sum(self._pod(item).ready for item in pods),
            observations=observations,
        )

    async def inject_failure(self, scenario: str) -> ClusterObservation:
        if scenario not in self._allowed_scenarios:
            raise KubernetesUnavailable("incident scenario is not allowlisted")
        if self.mode != "execute":
            raise KubernetesUnavailable("Kubernetes execution mode is not enabled")
        self._scope(self.namespace, self.workload)
        return await asyncio.to_thread(self._inject_sync, scenario)

    def _inject_sync(self, scenario: str) -> ClusterObservation:
        self._ensure_client()
        if self._load_error or self._apps is None:
            raise KubernetesUnavailable("Kubernetes is unavailable")
        try:
            deployment = self._apps.read_namespaced_deployment(self.workload, self.namespace)
            annotations = dict(getattr(deployment.metadata, "annotations", None) or {})
            containers = getattr(getattr(deployment.spec, "template", None).spec, "containers", [])
            if containers:
                annotations.setdefault("opspilot.io/healthy-image", str(getattr(containers[0], "image", "")))
                annotations.setdefault("opspilot.io/healthy-command", json.dumps(getattr(containers[0], "command", None)))
                readiness = getattr(containers[0], "readiness_probe", None)
                annotations.setdefault("opspilot.io/healthy-readiness", json.dumps(self._probe_dict(readiness)))
            patch: dict[str, Any] = {"metadata": {"annotations": annotations}}
            container_patch: dict[str, Any] = {"name": self.workload}
            if scenario == "deployment_failure":
                container_patch["image"] = "nginx:opspilot-image-does-not-exist"
            elif scenario == "pod_crash":
                container_patch["command"] = ["/bin/sh", "-c", "exit 1"]
            else:
                container_patch["readinessProbe"] = {"httpGet": {"path": "/opspilot-not-ready", "port": 80}, "periodSeconds": 2}
            patch["spec"] = {"template": {"spec": {"containers": [container_patch]}}}
            self._apps.patch_namespaced_deployment(self.workload, self.namespace, patch)
        except self._api_exception as exc:
            raise KubernetesUnavailable("controlled incident could not be injected") from exc
        return self._observe_sync(self.namespace, self.workload)

    async def execute_safe_action(self, action: str, target: str) -> bool:
        if self.mode != "execute":
            raise KubernetesUnavailable("Kubernetes execution mode is not enabled")
        return await asyncio.to_thread(self._execute_sync, action, target)

    def _execute_sync(self, action: str, target: str) -> bool:
        try:
            self.policy.validate_action(action)
        except PolicyViolation as exc:
            raise KubernetesUnavailable(str(exc)) from exc
        namespace, workload = self._target(target)
        self._ensure_client()
        if self._load_error or self._apps is None or self._core is None:
            raise KubernetesUnavailable("Kubernetes is unavailable")
        try:
            if action == "scale_deployment":
                self._apps.patch_namespaced_deployment(workload, namespace, {"spec": {"replicas": 1}})
            elif action == "rollback_deployment":
                self._rollback(workload, namespace)
            else:
                self._restart(workload, namespace)
            return True
        except self._api_exception as exc:
            raise KubernetesUnavailable("Kubernetes action failed") from exc

    def _target(self, target: str) -> tuple[str, str]:
        try:
            namespace, workload = self.policy.validate_target(target)
        except PolicyViolation as exc:
            raise KubernetesUnavailable(str(exc)) from exc
        self._scope(namespace, workload)
        return namespace, workload

    def _restart(self, workload: str, namespace: str) -> None:
        pods = self._core.list_namespaced_pod(namespace, label_selector=f"app.kubernetes.io/name={workload}").items
        if not pods:
            raise KubernetesUnavailable("no approved workload pod exists")
        selected = next((pod for pod in pods if not self._pod(pod).ready), pods[0])
        self._core.delete_namespaced_pod(selected.metadata.name, namespace, grace_period_seconds=5)

    def _rollback(self, workload: str, namespace: str) -> None:
        deployment = self._apps.read_namespaced_deployment(workload, namespace)
        annotations = getattr(deployment.metadata, "annotations", None) or {}
        image = annotations.get("opspilot.io/healthy-image")
        command_raw = annotations.get("opspilot.io/healthy-command")
        readiness_raw = annotations.get("opspilot.io/healthy-readiness")
        if not image:
            raise KubernetesUnavailable("no recorded healthy revision is available")
        container: dict[str, Any] = {"name": workload, "image": image}
        if command_raw:
            try:
                command = json.loads(command_raw)
                # A JSON null is intentional: it removes a crash-injection
                # command and restores the image's default entrypoint.
                container["command"] = command
            except json.JSONDecodeError:
                raise KubernetesUnavailable("recorded rollback target is invalid")
        if readiness_raw:
            try:
                readiness = json.loads(readiness_raw)
                # Preserve the original absence as an explicit field removal
                # when a rollout-failure probe was injected.
                container["readinessProbe"] = readiness
            except json.JSONDecodeError:
                raise KubernetesUnavailable("recorded readiness target is invalid")
        self._apps.patch_namespaced_deployment(workload, namespace, {"spec": {"template": {"spec": {"containers": [container]}}}})

    async def verify_health(self, target: str) -> bool:
        namespace, workload = self._target(target)
        observation = await self.observe(namespace, workload)
        return observation.health.status is HealthStatus.HEALTHY

    @staticmethod
    def _deployment(value: Any, namespace: str) -> DeploymentObservation:
        spec = getattr(value, "spec", None)
        status = getattr(value, "status", None)
        template_spec = getattr(getattr(spec, "template", None), "spec", None)
        containers = getattr(template_spec, "containers", []) or []
        image = getattr(containers[0], "image", None) if containers else None
        annotations = getattr(getattr(value, "metadata", None), "annotations", None) or {}
        desired = int(getattr(spec, "replicas", 0) or 0)
        available = int(getattr(status, "available_replicas", 0) or 0)
        ready = int(getattr(status, "ready_replicas", 0) or 0)
        updated = int(getattr(status, "updated_replicas", 0) or 0)
        return DeploymentObservation(
            name=str(getattr(getattr(value, "metadata", None), "name", "unknown")),
            namespace=namespace,
            desired_replicas=desired,
            available_replicas=available,
            ready_replicas=ready,
            updated_replicas=updated,
            revision=annotations.get("deployment.kubernetes.io/revision"),
            image=image,
            rollout_complete=desired > 0 and available >= desired and ready >= desired and updated >= desired,
        )

    @staticmethod
    def _pod(value: Any) -> PodObservation:
        status = getattr(value, "status", None)
        conditions = getattr(status, "conditions", None) or []
        ready = any(getattr(item, "type", "") == "Ready" and str(getattr(item, "status", "")) == "True" for item in conditions)
        waiting_reason = termination_reason = None
        restarts = 0
        for item in getattr(status, "container_statuses", None) or []:
            restarts += int(getattr(item, "restart_count", 0) or 0)
            state = getattr(item, "state", None)
            waiting_reason = waiting_reason or getattr(getattr(state, "waiting", None), "reason", None)
            termination_reason = termination_reason or getattr(getattr(state, "terminated", None), "reason", None)
        return PodObservation(
            name=str(getattr(getattr(value, "metadata", None), "name", "unknown")),
            phase=str(getattr(status, "phase", "Unknown")),
            ready=ready,
            restarts=restarts,
            waiting_reason=waiting_reason,
            termination_reason=termination_reason,
        )

    @staticmethod
    def _event(value: Any, workload: str) -> EventObservation:
        involved = getattr(value, "involved_object", None)
        timestamp = getattr(value, "last_timestamp", None) or getattr(value, "event_time", None)
        if timestamp and not isinstance(timestamp, datetime):
            timestamp = None
        return EventObservation(
            name=str(getattr(getattr(value, "metadata", None), "name", "event")),
            reason=getattr(value, "reason", None),
            event_type=getattr(value, "type", None),
            message=str(getattr(value, "message", "") or "")[:800],
            involved_object=getattr(involved, "name", None) or workload,
            observed_at=timestamp,
        )

    @staticmethod
    def _event_matches(value: Any, workload: str) -> bool:
        involved = getattr(value, "involved_object", None)
        name = getattr(involved, "name", None)
        return not name or name == workload or name.startswith(f"{workload}-")

    @staticmethod
    def _assess(deployment: DeploymentObservation, pods: list[PodObservation]) -> HealthAssessment:
        reasons: list[str] = []
        if deployment.desired_replicas <= 0:
            reasons.append("deployment has no desired replicas")
        elif deployment.available_replicas < deployment.desired_replicas:
            reasons.append(f"available replicas {deployment.available_replicas}/{deployment.desired_replicas}")
        if not deployment.rollout_complete:
            reasons.append("deployment rollout is incomplete")
        for pod in pods:
            if not pod.ready:
                state = pod.waiting_reason or pod.termination_reason or pod.phase
                reasons.append(f"pod {pod.name} is not ready ({state})")
            if pod.restarts > 0:
                reasons.append(f"pod {pod.name} has {pod.restarts} restart(s)")
        return HealthAssessment(status=HealthStatus.HEALTHY if not reasons else HealthStatus.DEGRADED, reasons=list(dict.fromkeys(reasons))[:8])

    @staticmethod
    def _probe_dict(probe: Any) -> Mapping[str, Any] | None:
        if probe is None:
            return None
        if hasattr(probe, "to_dict"):
            value = probe.to_dict()
            if not isinstance(value, Mapping):
                return None
            def camelize(item: Any) -> Any:
                if isinstance(item, Mapping):
                    return {
                        re.sub(r"_([a-z])", lambda match: match.group(1).upper(), str(key)): camelize(child)
                        for key, child in item.items()
                        if child is not None
                    }
                if isinstance(item, list):
                    return [camelize(child) for child in item]
                return item
            return camelize(value)
        return None
