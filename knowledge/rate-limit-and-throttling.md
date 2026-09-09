# Rate limiting and provider throttling

Use this runbook when 429 responses, quota errors, or burst traffic cause otherwise healthy requests to fail.

Separate client-level throttling from provider quota exhaustion. Compare request rate, concurrency, retry-after headers, token or quota consumption, and the affected tenant or endpoint. Unbounded retries turn a short throttle event into a longer outage.

Honor retry-after, add bounded exponential backoff, and reduce non-critical traffic before changing a limit. Verify successful request rate, quota headroom, and downstream latency after the observation window.
