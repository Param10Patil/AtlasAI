# Kubernetes readiness and liveness failures

Use this runbook when a pod is running but the service is not receiving traffic, or when a rollout stalls on readiness checks.

Start with the deployment and pod events. Compare readiness and liveness probe paths, ports, initial delays, and recent image or configuration changes. A pod can be healthy from the process perspective while still failing a dependency check in its readiness probe.

Prefer a reversible action: pause the rollout, inspect the failing probe response, and compare the last known-good revision. Do not delete healthy replicas. After a reviewed change, verify ready replicas, endpoint membership, and the service health endpoint.
