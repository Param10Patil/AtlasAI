# Observability gaps during an incident

Use this runbook when an incident is reported but logs, metrics, traces, or ownership metadata are incomplete.

Record the affected service, first-seen time, user-visible symptom, recent changes, and a representative request or correlation identifier with secrets removed. Check whether the health endpoint, error rate, latency, saturation, and dependency signals are available for the same time window.

If evidence remains incomplete, lower confidence and keep the recommendation advisory. Do not infer a root cause from an empty dashboard. Create a follow-up to add the missing signal and preserve the original incident timeline.
