# Cache saturation and eviction pressure

Use this runbook when cache hit rate falls, evictions rise, or downstream latency increases after a traffic or key-cardinality change.

Check memory utilization, eviction counters, key-size distribution, TTL policy, and the ratio of misses to requests. Confirm whether a deployment changed serialization or cache-key cardinality. A cache flush is not a first response because it can amplify load on the backing store.

Mitigate with a reviewed TTL or capacity adjustment, rate-limit a noisy caller, and watch the backing service before restoring traffic. Verify hit rate, evictions, dependency latency, and error rate over a stable observation window.
