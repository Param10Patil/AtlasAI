# Local Kubernetes demo

OpsPilot uses Kubernetes as the source of truth for its SRE demo. The local
topology is intentionally small: one OpsPilot API pod, one PostgreSQL
StatefulSet, and the protected `ops-demo/checkout-api` workload. The triage,
knowledge, resolution, MCP, and remediation boundaries run inside the API
image; the legacy role manifests are not part of the default deployment.

## Prerequisites

Use a local kind or k3d cluster with `kubectl` and Docker available. This
checkout does not include a reachable cluster, so the commands below are a
runbook rather than a claim that a cluster rollout was verified here.

## Deploy the demo

```powershell
# Build and load the single application image (kind example)
docker build -t opspilot:dev .
kind load docker-image opspilot:dev

# Create the API namespace and database resources
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.example.yaml   # replace values locally first
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/postgres-service.yaml
kubectl apply -f k8s/postgres-statefulset.yaml

# Create the protected workload and the least-privilege API identity
kubectl apply -f k8s/demo-namespace.yaml
kubectl apply -f k8s/demo-workload.yaml
kubectl apply -f k8s/api-serviceaccount.yaml
kubectl apply -f k8s/demo-role.yaml
kubectl apply -f k8s/api-service.yaml
kubectl apply -f k8s/api-deployment.yaml

kubectl rollout status statefulset/postgres -n opspilot --timeout=120s
kubectl rollout status deployment/checkout-api -n ops-demo --timeout=120s
kubectl rollout status deployment/api -n opspilot --timeout=120s
kubectl auth can-i get deployments --as=system:serviceaccount:opspilot:opspilot-api -n ops-demo
kubectl auth can-i patch deployments --as=system:serviceaccount:opspilot:opspilot-api -n ops-demo
kubectl auth can-i delete pods --as=system:serviceaccount:opspilot:opspilot-api -n ops-demo
```

The API ConfigMap sets `KUBERNETES_MODE=execute` and
`REMEDIATION_MODE=execute`, but `AUTO_REMEDIATION=false`. Every action remains
manual unless a user explicitly enables auto-remediation in the request.
RBAC grants reads for pods, events, deployments, and ReplicaSets, plus only
pod deletion and deployment patching in `ops-demo`; there is no shell or
arbitrary `kubectl` path.

## Run the closed-loop demo

Port-forward the API, open the UI, and click **Inject controlled incident**.
The injector changes only `ops-demo/checkout-api` using the Kubernetes API.
OpsPilot then observes the resulting readiness/replica/restart/event signals,
detects a generic health-policy violation, retrieves MCP/RAG evidence, ranks a
safe action, and waits for approval. Click **Approve & remediate** to execute
the allowlisted action and wait for the after-observation to report healthy.

```powershell
kubectl port-forward service/api 8080:8080 -n opspilot
Start-Process http://127.0.0.1:8080
```

Use `GET /api/cluster/observations` to inspect the current structured facts or
`POST /api/cluster/detect` to request a bounded detection pass. The four
possible actions are `restart_pod`, `scale_deployment`,
`rollback_deployment`, and `clear_temporary_condition`; scale is fixed to one
replica and rollback uses the deployment's recorded healthy revision.

## Cloud Run distinction

Cloud Run keeps `min instances=0` and disables Kubernetes execution. It is a
request-driven analysis surface, not a passive cluster monitor. The real
closed-loop remediation demo requires this local Kubernetes deployment (or a
deliberately connected cluster with equivalent RBAC); it does not add a
monitoring container or an MLflow server.
