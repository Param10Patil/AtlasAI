# Database connection exhaustion

**Symptoms:** timeouts acquiring a connection, elevated latency, or a pool at its limit.

**Investigate:** compare pool usage with database capacity, find leaked transactions, and check whether a dependency is retrying too aggressively.

**Safe actions:** reduce load or tune a bounded pool after review. Preserve evidence before restarting anything.

**Warnings:** killing sessions can lose work and requires database-owner approval.
