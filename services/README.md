# Logical service boundaries

Compose and Kubernetes may run `triage`, `knowledge`, and `resolution` as
small HTTP processes. Development and Cloud Run use the same ports in one
`in_process` application. These directories remain boundary documentation
until the service pass adds adapters; no duplicate business logic is planned.
