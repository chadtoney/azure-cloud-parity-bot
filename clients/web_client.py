"""General-purpose async web content client for non-Learn sources."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from loguru import logger

from config.settings import settings


class WebContentClient:
    """Async HTTP client for fetching content from arbitrary public URLs."""

    AZURE_UPDATES_URL = "https://azure.microsoft.com/en-us/updates/"
    SOVEREIGN_DOCS_URLS: List[str] = [
        "https://azure.microsoft.com/en-us/explore/global-infrastructure/government/",
        "https://azure.microsoft.com/en-us/explore/global-infrastructure/sovereign-clouds/",
    ]

    DEFAULT_HEADERS = {
        "User-Agent": "azure-cloud-parity-bot/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "WebContentClient":
        self._client = httpx.AsyncClient(
            headers=self.DEFAULT_HEADERS,
            timeout=self._timeout,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch(self, url: str) -> str:
        """Fetch raw HTML from any URL."""
        assert self._client is not None, "Use as async context manager."
        for attempt in range(1, settings.scrape_max_retries + 1):
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as exc:
                logger.warning(f"[attempt {attempt}] HTTP {exc.response.status_code} for {url}")
                if exc.response.status_code in {401, 403, 404}:
                    break
            except httpx.RequestError as exc:
                logger.warning(f"[attempt {attempt}] Request error for {url}: {exc}")
            await asyncio.sleep(attempt * settings.scrape_delay_seconds)
        return ""

    async def fetch_many(self, urls: List[str]) -> Dict[str, str]:
        """Fetch multiple URLs concurrently, returning a map of url → html."""
        results: Dict[str, str] = {}
        for url in urls:
            html = await self.fetch(url)
            if html:
                results[url] = html
            await asyncio.sleep(settings.scrape_delay_seconds)
        return results

    async def fetch_azure_updates(self) -> str:
        """Fetch the Azure Updates blog/feed."""
        return await self.fetch(self.AZURE_UPDATES_URL)

    async def fetch_sovereign_docs(self) -> Dict[str, str]:
        """Fetch Azure sovereign cloud landing pages."""
        return await self.fetch_many(self.SOVEREIGN_DOCS_URLS)
