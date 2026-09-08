'''Policy-gated remediation agent using only MCP safe-action tools.'''

from typing import ClassVar, Protocol

from app.remediation.contracts import RemediationContext, RemediationResult, SafeAction


class RemediationToolClient(Protocol):
    async def execute_safe_action(self, action: str, target: str) -> dict[str, object]: ...

    async def verify_health(self, target: str) -> dict[str, object]: ...


class SimulatedActionExecutor:
    '''Credential-free executor used for demos; never invokes a shell command.'''

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

    def __init__(self, mcp_client: RemediationToolClient):
        self.mcp_client = mcp_client

    async def remediate(self, context: RemediationContext, *, enabled: bool) -> RemediationResult:
        action = self._actions.get(context.category)
        target = context.service or 'affected-service'
        if action is None:
            return RemediationResult(
                status='disabled',
                target=target,
                message='No allowlisted remediation exists for this incident category.',
            )
        if not enabled:
            return RemediationResult(
                status='awaiting_approval',
                action=action,
                target=target,
                message='A safe action was prepared and is waiting for the policy gate.',
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
                )
            verification = await self.mcp_client.verify_health(target)
            healthy = verification.get('status') == 'healthy'
            return RemediationResult(
                status='verified' if healthy else 'failed',
                action=action,
                target=target,
                message='The safe action completed and health was verified.' if healthy else 'The safe action completed but health is not verified.',
                health_verified=healthy,
                retry_recommended=not healthy,
            )
        except Exception:  # noqa: BLE001
            return RemediationResult(
                status='failed',
                action=action,
                target=target,
                message='The remediation service was unavailable.',
                retry_recommended=True,
            )
