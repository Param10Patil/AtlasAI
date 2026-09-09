"""Explicit allowlist for infrastructure actions.

The policy is deliberately boring: an agent may choose only one of four
named actions against one configured demo workload.  It is authorization
data, not a prompt, so it is enforced before any Kubernetes API call.
"""

from dataclasses import dataclass, field


class PolicyViolation(ValueError):
    """A target or action is outside the remediation policy."""


@dataclass(frozen=True)
class ActionPolicy:
    allowed_namespace: str = "ops-demo"
    allowed_targets: frozenset[str] = field(default_factory=lambda: frozenset({"checkout-api"}))
    allowed_actions: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"restart_pod", "scale_deployment", "rollback_deployment", "clear_temporary_condition"}
        )
    )
    allowed_resource_types: frozenset[str] = field(default_factory=lambda: frozenset({"deployment", "pod"}))
    maximum_scale: int = 1
    rollback_allowed: bool = True
    restart_allowed: bool = True

    def validate_action(self, action: str) -> None:
        if action not in self.allowed_actions:
            raise PolicyViolation("action is not allowlisted")
        if action == "rollback_deployment" and not self.rollback_allowed:
            raise PolicyViolation("rollback is disabled by policy")
        if action in {"restart_pod", "clear_temporary_condition"} and not self.restart_allowed:
            raise PolicyViolation("restart is disabled by policy")

    def validate_target(self, target: str) -> tuple[str, str]:
        namespace, separator, workload = target.partition("/")
        if not separator or namespace != self.allowed_namespace:
            raise PolicyViolation("target namespace is outside the approved scope")
        if workload not in self.allowed_targets:
            raise PolicyViolation("target workload is outside the approved scope")
        return namespace, workload
