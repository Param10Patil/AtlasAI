# Queue backlog and worker starvation

Use this runbook when queue depth grows, oldest-message age increases, or workers report timeouts while the producer remains healthy.

Compare enqueue and completion rates, worker concurrency, retry counts, visibility timeouts, and downstream dependency latency. Look for a poison message, a deployment that reduced worker capacity, or retry amplification.

Pause unnecessary retries before scaling. A safe recovery can restart one failed worker or scale the deployment back to one known-good replica for a controlled check. Verify queue depth, oldest-message age, worker health, and downstream error rate before declaring recovery.
