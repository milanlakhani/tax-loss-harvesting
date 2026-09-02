from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from app.domain.errors import ProviderError


class HttpJsonClient:
    def __init__(self, client: httpx.AsyncClient, *, retries: int = 2, provider: str) -> None:
        self.client = client
        self.retries = retries
        self.provider = provider

    async def get_json(self, url: str, *, headers: dict | None = None, params: dict | None = None) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.get(url, headers=headers, params=params)
                if response.status_code == 429:
                    await asyncio.sleep(0.05 * (attempt + 1))
                    last_error = ProviderError("rate limited", self.provider)
                    continue
                if response.status_code >= 500:
                    last_error = ProviderError(f"upstream {response.status_code}", self.provider)
                    await asyncio.sleep(0.05 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) and not isinstance(payload, list):
                    raise ProviderError("malformed payload", self.provider)
                return payload if isinstance(payload, dict) else {"data": payload}
            except httpx.TimeoutException as exc:
                last_error = ProviderError("timeout", self.provider)
                last_error.__cause__ = exc
            except httpx.HTTPError as exc:
                last_error = ProviderError("unavailable", self.provider)
                last_error.__cause__ = exc
        raise last_error or ProviderError("unavailable", self.provider)


def parse_decimal(value: object, provider: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ProviderError("malformed numeric", provider) from exc


def parse_timestamp(value: object, fallback: datetime | None = None) -> datetime:
    if value is None:
        return fallback or datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return fallback or datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
