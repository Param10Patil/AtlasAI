# Local Kubernetes foundation

These manifests describe one educational namespace with one API pod, three
logical service boundaries, and one pgvector-backed PostgreSQL StatefulSet.
The API is the only public entry point. The service deployments intentionally
reuse the application image; the service phase will wire their HTTP adapters.

secret.example.yaml is a template only. Create a local Secret out of band and
never commit real credentials. A kind or k3d cluster is the target; cloud
deployment uses Cloud Run and does not require these manifests.
