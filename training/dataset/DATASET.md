# Incident dataset v1.0.0

`incidents.jsonl` contains 120 synthetic examples: exactly 20 each for
`deployment_failure`, `database_failure`, `authentication_failure`,
`network_failure`, `performance_issue`, and `availability_issue`.

Provenance is `synthetic`: examples were authored from the runbook vocabulary,
then manually reviewed for label agreement, operational safety, and absence of
credentials or real incident identifiers. The training command records the
source checksum, seed, split counts, and label ordering in
`training_metadata.json`.

This is an educational classifier dataset, not production accuracy evidence.
It is small, templated, English-only, and may not represent organization-
specific terminology, severity, or novel failure modes. Duplicate text is
rejected before splitting so validation metrics cannot silently include train
copies. Add a new dataset version and checksum when examples change.
