# Rollback safety

**Purpose:** provide a checklist for a human-approved rollback when evidence points to a release regression.

**Checklist:** identify the target known-good revision, confirm data compatibility, record the decision, watch health checks, and define a forward-fix owner.

**Warnings:** rollback is not automatically safe for irreversible schema changes or one-way migrations.
