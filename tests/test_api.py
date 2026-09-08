import asyncio
import json
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.runtime import build_runtime


def test_health_ready_and_analysis_contract():
    runtime = asyncio.run(build_runtime())
    with TestClient(create_app(runtime)) as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/ready').status_code == 200
        response = client.post('/api/incidents/analyze', json={'description': 'payment API returns 503 after deployment'})
        assert response.status_code == 200
        payload = response.json()
        assert payload['analysis']['severity'] == 'high'
        assert payload['analysis']['evidence']
        assert 'prompt' not in str(payload).lower()


def test_invalid_input_is_rejected():
    runtime = asyncio.run(build_runtime())
    with TestClient(create_app(runtime)) as client:
        assert client.post('/api/incidents/analyze', json={'description': ' '}).status_code == 422
        assert client.post('/api/incidents/analyze', json={'description': 'ok', 'extra': True}).status_code == 422


def test_job_contract_is_idempotent_and_streams_ndjson():
    runtime = asyncio.run(build_runtime())
    request_id = '11111111-1111-4111-8111-111111111111'
    with TestClient(create_app(runtime)) as client:
        response = client.post(
            '/api/incidents/analyze/jobs',
            json={'description': 'payment API returns 503 after deployment', 'client_request_id': request_id},
        )
        assert response.status_code == 202
        job = response.json()
        assert UUID(job['job_id'])
        duplicate = client.post(
            '/api/incidents/analyze/jobs',
            json={'description': 'ignored duplicate', 'client_request_id': request_id},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()['job_id'] == job['job_id']
        events = client.get(job['events_url'])
        assert events.status_code == 200
        lines = [json.loads(line) for line in events.text.splitlines() if line]
        assert [line['status'] for line in lines][-1] == 'complete'
        assert lines[-1]['result']['analysis']['evidence']
        status = client.get(job['status_url']).json()
        assert status['status'] == 'complete'
