'''Lightweight deterministic and HTTP embedding providers.'''

import asyncio
import hashlib
import json
import math
from collections.abc import Sequence
from urllib.request import Request, urlopen


class HashEmbeddingProvider:
    '''Stable tiny vectors for local runs and tests; no model download required.'''

    dimension = 8

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = [token for token in text.lower().split() if token]
        for token in tokens:
            digest = hashlib.sha256(token.encode('utf-8')).digest()
            index = digest[0] % self.dimension
            sign = 1.0 if digest[1] % 2 else -1.0
            vector[index] += sign
        length = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [round(value / length, 8) for value in vector]


class HTTPEmbeddingProvider:
    '''OpenAI-compatible embeddings endpoint with an explicit timeout.'''

    def __init__(self, base_url: str, api_key: str, model: str, timeout_seconds: float = 20):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.dimension = 0

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._request, list(texts))

    async def embed_query(self, text: str) -> list[float]:
        rows = await asyncio.to_thread(self._request, [text])
        return rows[0]

    def _request(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({'model': self.model, 'input': texts}).encode('utf-8')
        request = Request(
            f'{self.base_url}/embeddings',
            data=payload,
            headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'},
            method='POST',
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode('utf-8'))
        vectors = [row['embedding'] for row in sorted(data['data'], key=lambda item: item.get('index', 0))]
        if not vectors or any(not isinstance(row, list) for row in vectors):
            raise ValueError('embedding provider returned no vectors')
        dimensions = len(vectors[0])
        if any(len(row) != dimensions for row in vectors):
            raise ValueError('embedding provider returned inconsistent dimensions')
        self.dimension = dimensions
        return vectors
