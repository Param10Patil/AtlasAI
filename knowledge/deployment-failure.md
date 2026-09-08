# Deployment failure

**Symptoms:** a new revision never becomes ready, crashes on startup, or fails health checks.

**Investigate:** inspect the image digest, configuration validation, migration status, and startup logs with access controls.

**Safe actions:** compare with the last known-good revision and ask an operator to choose a rollback or forward fix.

**Warnings:** never infer that a restart fixes a bad image or destructive migration.
