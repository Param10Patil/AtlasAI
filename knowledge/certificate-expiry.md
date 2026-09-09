# TLS certificate and trust failures

Use this runbook when clients report TLS handshake errors, certificate expiration, hostname mismatch, or a sudden increase in secure-connect failures.

Inspect certificate not-before and not-after timestamps, the complete served chain, hostname coverage, and the trust store used by the affected client. Compare the active secret or load-balancer certificate with the last known-good version. Never paste private keys or credentials into an incident description.

Rotate through the normal reviewed secret process, then verify the chain from an affected client, the service health endpoint, and dependent services. Keep rollback available until handshake errors remain at baseline.
