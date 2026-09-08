# Local Kubernetes foundation

These manifests describe one educational namespace with one API pod, three
logical service boundaries, and one pgvector-backed PostgreSQL StatefulSet.
The API is the only public entry point. The service deployments intentionally
reuse the application image and expose the same logical boundaries documented
by the FastAPI internal routes; they are an educational topology rather than
independent implementations.

secret.example.yaml is a template only. Create a local Secret out of band and
never commit real credentials. A kind or k3d cluster is the target; cloud
deployment uses Cloud Run and does not require these manifests.
