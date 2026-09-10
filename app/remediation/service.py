'''Policy-gated remediation agent using only MCP safe-action tools.'''

import re
from typing import ClassVar, Protocol

from app.kubernetes.contracts import ClusterObservation
from app.remediation.contracts import (
    ExecutionPreview,
    RemediationContext,
    RemediationResult,
    SafeAction,
)


class RemediationToolClient(Protocol):
    async def execute_safe_action(self, action: str, target: str) -> dict[str, object]: ...

    async def verify_health(self, target: str) -> dict[str, object]: ...


class SimulatedActionExecutor:
    '''Credential-free executor used for demos; never invokes a shell command.'''

    simulated = True

    def __init__(self):
        self.executions: list[tuple[str, str]] = []

    async def execute_safe_action(self, action: str, target: str) -> bool:
        self.executions.append((action, target))
        return True

    async def verify_health(self, target: str) -> bool:
        return bool(target)


class RemediationAgent:
    '''Select one allowlisted action, execute through MCP, then verify health.'''

    _actions: ClassVar[dict[str, SafeAction]] = {
        'deployment_failure': SafeAction.ROLLBACK_DEPLOYMENT,
        'availability_issue': SafeAction.RESTART_POD,
        'database_failure': SafeAction.CLEAR_TEMPORARY_CONDITION,
        'performance_issue': SafeAction.SCALE_DEPLOYMENT,
        'network_failure': SafeAction.CLEAR_TEMPORARY_CONDITION,
        'authentication_failure': SafeAction.CLEAR_TEMPORARY_CONDITION,
    }
    _preview_operations: ClassVar[dict[SafeAction, tuple[str, str, str, str]]] = {
        SafeAction.RESTART_POD: (
            'Restart the selected unhealthy pod for the approved workload.',
            'Pod (selected from Deployment)',
            'CoreV1Api.delete_namespaced_pod(..., grace_period_seconds=5)',
            'restart pod',
        ),
        SafeAction.SCALE_DEPLOYMENT: (
            'Patch the approved deployment to one replica.',
            'Deployment',
            'AppsV1Api.patch_namespaced_deployment(..., {"spec": {"replicas": 1}})',
            'scale deployment to one',
        ),
        SafeAction.ROLLBACK_DEPLOYMENT: (
            'Patch the deployment pod template back to its recorded healthy revision.',
            'Deployment pod template',
            'AppsV1Api.patch_namespaced_deployment(..., {"spec": {"template": ...}})',
            'rollback deployment',
        ),
        SafeAction.CLEAR_TEMPORARY_CONDITION: (
            'Patch the approved deployment to remove or restore its recorded temporary condition.',
            'Deployment pod template',
            'AppsV1Api.patch_namespaced_deployment(..., {"spec": {"template": ...}})',
            'clear temporary condition',
        ),
    }

    def __init__(self, mcp_client: RemediationToolClient):
        self.mcp_client = mcp_client

    @classmethod
    def _select_action(cls, context: RemediationContext) -> SafeAction | None:
        # A deployment failure is a release-level regression. Prefer the
        # recorded last-known-good revision even when stale pod restart events
        # make a restart appear first in a ranked textual plan. This keeps the
        # category policy authoritative and avoids restarting a pod onto the
        # same broken template.
        category_action = cls._actions.get(context.category)
        if context.category == 'deployment_failure':
            return category_action
        for recommendation in sorted(context.recommended_actions, key=lambda item: item.rank):
            text = recommendation.text.lower()
            if 'rollback' in text:
                return SafeAction.ROLLBACK_DEPLOYMENT
            if 'restart' in text:
                return SafeAction.RESTART_POD
            if 'scale' in text:
                return SafeAction.SCALE_DEPLOYMENT
            if 'clear' in text and 'temporary' in text:
                return SafeAction.CLEAR_TEMPORARY_CONDITION
        return category_action

    @classmethod
    def _preview(cls, context: RemediationContext, action: SafeAction, target: str, *, enabled: bool) -> ExecutionPreview:
        operation, resource_type, api_method, _ = cls._preview_operations[action]
        namespace = context.observation.namespace if context.observation else target.partition('/')[0] or 'not observed'
        observation = context.observation
        why = f"{context.category.replace('_', ' ')} selected by the ranked policy plan."
        if observation:
            deployment = observation.deployment
            state = (
                f"Fresh observation: {deployment.ready_replicas}/{deployment.desired_replicas} "
                f"ready replicas, {len(observation.pods)} observed pod(s), health "
                f"{observation.health.status.value}."
                if deployment else f"Fresh observation: workload health {observation.health.status.value}."
            )
            if action is SafeAction.RESTART_POD:
                selected_pod = next((pod for pod in observation.pods if not pod.ready), None) or (observation.pods[0] if observation.pods else None)
                if selected_pod:
                    operation = f"Delete pod {selected_pod.name} through the Kubernetes API so its controller recreates it. {state}"
                else:
                    operation = f"Restart the selected unhealthy pod for the approved workload. {state}"
            else:
                operation = f"{operation} {state}"
            why = f"{context.category.replace('_', ' ')}; {state}"
        selected_ids = {identifier for item in context.recommended_actions if item.rank == 1 for identifier in item.evidence_ids}
        evidence = [item for item in context.evidence if not selected_ids or item.id in selected_ids][:3]
        commands: list[str] = []
        for item in evidence:
            for line in item.excerpt.splitlines():
                match = re.search(r'(?<![\w-])(kubectl\s+[^\n]+)', line, re.IGNORECASE)
                if match:
                    command = match.group(1).strip().rstrip(' .')
                    if command not in commands:
                        commands.append(command)
        return ExecutionPreview(
            action=action,
            category=context.category,
            target=target,
            namespace=namespace,
            resource_type=resource_type,
            why=why,
            operation=operation,
            execution_method='MCP execute_safe_action -> Kubernetes Python client',
            api_method=api_method,
            rag_evidence=evidence,
            rag_commands=commands[:3],
            policy_result='Allowlisted action; policy gate passed' if enabled else 'Allowlisted action; explicit operator approval required',
            approval_required=not enabled,
        )

    async def remediate(self, context: RemediationContext, *, enabled: bool) -> RemediationResult:
        action = self._select_action(context)
        target = (
            f'{context.observation.namespace}/{context.observation.workload}'
            if context.observation
            else context.service or 'affected-service'
        )
        if action is None:
            return RemediationResult(
                status='disabled',
                target=target,
                message='No allowlisted remediation exists for this incident category.',
            )
        preview = self._preview(context, action, target, enabled=enabled)
        if not enabled:
            return RemediationResult(
                status='awaiting_approval',
                action=action,
                target=target,
                message='A safe action was prepared and is waiting for the policy gate.',
                before_observation=context.observation,
                execution_preview=preview,
            )
        try:
            execution = await self.mcp_client.execute_safe_action(action.value, target)
            if execution.get('status') != 'executed':
                return RemediationResult(
                    status='failed',
                    action=action,
                    target=target,
                    message='The allowlisted action was not executed.',
                    retry_recommended=True,
                    execution_preview=preview,
                )
            if context.observation and context.observation.connection.value == 'connected' and execution.get('simulated'):
                return RemediationResult(
                    status='awaiting_approval',
                    action=action,
                    target=target,
                    message='A simulated executor cannot change a connected cluster; no action was applied.',
                    health_verified=False,
                    before_observation=context.observation,
                    execution_preview=preview,
                )
            verification = await self.mcp_client.verify_health(target)
            healthy = verification.get('status') == 'healthy'
            after_observation = None
            if isinstance(verification.get('observation'), dict):
                try:
                    after_observation = ClusterObservation.model_validate(verification['observation'])
                except ValueError:
                    after_observation = None
            mode_note = ' in simulation' if execution.get('simulated') else ''
            return RemediationResult(
                status='verified' if healthy else 'failed',
                action=action,
                target=target,
                message=f'The allowlisted action completed{mode_note}; health was verified.' if healthy else 'The safe action completed but health is not verified.',
                health_verified=healthy,
                retry_recommended=not healthy,
                before_observation=context.observation,
                after_observation=after_observation,
                execution_preview=preview,
            )
        except Exception:  # noqa: BLE001
            return RemediationResult(
                status='failed',
                action=action,
                target=target,
                message='The remediation service was unavailable.',
                retry_recommended=True,
                execution_preview=preview,
            )
