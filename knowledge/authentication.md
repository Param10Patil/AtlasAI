# Authentication failures

**Symptoms:** valid users receive 401 or 403 responses after a configuration or identity change.

**Investigate:** verify token audience, clock skew, key rotation, and authorization policy version without logging credentials.

**Safe actions:** revert a confirmed configuration change through the normal change process.

**Warnings:** never request or print tokens, passwords, or secret values in an investigation.
