"""Build the larger, deterministic incident dataset used by quality gates."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from pathlib import Path

try:
    from training.train_lora import LABELS, _dataset_sha256, _load_rows
except ModuleNotFoundError:  # supports `python training/dataset/build_v2.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from training.train_lora import LABELS, _dataset_sha256, _load_rows


EXTRA_ROWS: tuple[tuple[str, str], ...] = (
    ("The checkout deployment crashes while loading its configuration.", "deployment_failure"),
    ("A new replica fails its liveness probe during rollout.", "deployment_failure"),
    ("The release controller reports a progressing deadline exceeded.", "deployment_failure"),
    ("The application image starts and exits with a nonzero status.", "deployment_failure"),
    ("A missing secret prevents the deployed API from initializing.", "deployment_failure"),
    ("The canary deployment has no available replacement pods.", "deployment_failure"),
    ("The worker image cannot pass its startup health check.", "deployment_failure"),
    ("A release configuration error leaves the new pods pending.", "deployment_failure"),
    ("The deployment controller cannot schedule the updated container.", "deployment_failure"),
    ("A failed init container blocks the checkout rollout.", "deployment_failure"),
    ("The database pool reaches its configured maximum.", "database_failure"),
    ("PostgreSQL is unreachable from the checkout API.", "database_failure"),
    ("SQL requests wait for a connection until they time out.", "database_failure"),
    ("The orders service receives a database connection reset.", "database_failure"),
    ("A migration leaves the application unable to open a session.", "database_failure"),
    ("The persistence service refuses the API connection attempt.", "database_failure"),
    ("Database sessions are leaked and no slots remain.", "database_failure"),
    ("The checkout API cannot authenticate to its PostgreSQL instance.", "database_failure"),
    ("A database failover causes connection acquisition errors.", "database_failure"),
    ("The SQL dependency is unavailable during request handling.", "database_failure"),
    ("Service discovery cannot resolve the billing endpoint.", "network_failure"),
    ("Checkout pods time out while contacting inventory.", "network_failure"),
    ("A cluster route drops traffic to the payment namespace.", "network_failure"),
    ("The upstream connection is refused from the worker pod.", "network_failure"),
    ("An updated network policy blocks checkout egress.", "network_failure"),
    ("The internal hostname has no DNS answer.", "network_failure"),
    ("TLS negotiation fails between the API and its dependency.", "network_failure"),
    ("The service mesh sidecar cannot reach the upstream.", "network_failure"),
    ("A firewall rule interrupts calls to the webhook host.", "network_failure"),
    ("Cross-namespace requests lose their network path.", "network_failure"),
    ("Checkout p95 latency remains above the service objective.", "performance_issue"),
    ("The request queue grows while workers process slowly.", "performance_issue"),
    ("API calls take much longer after the feature flag changed.", "performance_issue"),
    ("The service spends most of its time waiting on CPU.", "performance_issue"),
    ("A p99 latency alert fired for the checkout route.", "performance_issue"),
    ("Search requests exceed their five second deadline.", "performance_issue"),
    ("The worker backlog increases despite normal traffic.", "performance_issue"),
    ("Response time is above the error budget for ten minutes.", "performance_issue"),
    ("Slow SQL makes the customer request path exceed its budget.", "performance_issue"),
    ("The API is processing fewer requests per second than usual.", "performance_issue"),
    ("Customers receive an outage response from the checkout endpoint.", "availability_issue"),
    ("The gateway has no ready backend for the public API.", "availability_issue"),
    ("All checkout requests fail with a service unavailable status.", "availability_issue"),
    ("The regional API health check is down.", "availability_issue"),
    ("The application is not reachable from the public ingress.", "availability_issue"),
    ("A dependency outage leaves checkout unable to serve traffic.", "availability_issue"),
    ("The status endpoint reports a complete service outage.", "availability_issue"),
    ("Users cannot access the API in the affected region.", "availability_issue"),
    ("The gateway marks every upstream as unhealthy.", "availability_issue"),
    ("Availability dropped after the latest configuration rollout.", "availability_issue"),
    ("The login service rejects a valid access token.", "authentication_failure"),
    ("Users receive 401 responses after the identity provider update.", "authentication_failure"),
    ("JWT verification fails because the signing key is unknown.", "authentication_failure"),
    ("The OAuth redirect returns an authorization error.", "authentication_failure"),
    ("A valid session is denied by the authorization middleware.", "authentication_failure"),
    ("The token issuer setting no longer matches the identity service.", "authentication_failure"),
    ("Existing users cannot complete the sign-in flow.", "authentication_failure"),
    ("The admin permission check returns forbidden for every account.", "authentication_failure"),
    ("Access tokens expire immediately after login.", "authentication_failure"),
    ("A login configuration regression blocks protected routes.", "authentication_failure"),
)


def build(source: Path, destination: Path) -> dict[str, object]:
    rows, _ = _load_rows(source)
    existing = {row['text'] for row in rows}
    additions = [{'text': text, 'label': label} for text, label in EXTRA_ROWS]
    if any(row['text'] in existing for row in additions):
        raise ValueError('v2 additions contain text already present in v1')
    if len({row['text'] for row in additions}) != len(additions) or {row['label'] for row in additions} != set(LABELS):
        raise ValueError('v2 additions must be unique and cover every label')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('w', encoding='utf-8', newline='\n') as stream:
        for row in rows + additions:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
    validated, diagnostics = _load_rows(destination)
    return {
        'source': str(source), 'output': str(destination), 'rows': len(validated),
        'class_counts': diagnostics['class_counts'], 'dataset_sha256': _dataset_sha256(destination),
        'added_rows': len(additions),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=root / 'training/dataset/incidents.jsonl')
    parser.add_argument('--output', type=Path, default=root / 'training/dataset/incidents-v2.jsonl')
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
