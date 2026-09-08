import asyncio

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
