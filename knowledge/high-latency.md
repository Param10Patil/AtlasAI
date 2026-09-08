# High latency

**Symptoms:** p95 or p99 latency breaches its objective while error rate may remain normal.

**Investigate:** compare endpoint and dependency timings, saturation, recent releases, and retry amplification.

**Safe actions:** reduce an identified expensive path or roll back a confirmed regression after approval.

**Warnings:** averages can hide a small but severe tail; use bounded, representative measurements.
