"""Microsoft Learn MCP client – wraps the MS Learn MCP server for doc fetching."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from config.settings import settings


class MicrosoftLearnMCPClient:
    """
    Async client for fetching Azure documentation from Microsoft Learn.

    In a full MCP deployment this would speak the Model Context Protocol
    (JSON-RPC over stdio or HTTP) to the ms-learn MCP server.
    For portability the class falls back to direct HTTPS requests when no
    MCP server is configured.
    """

    DEFAULT_HEADERS = {
        "User-Agent": "azure-cloud-parity-bot/1.0 (+https://github.com/your-org/azure-cloud-parity-bot)",
        "Accept": "text/html,application/xhtml+xml",
    }

    # Well-known parity pages on Microsoft Learn
    GOVERNMENT_PARITY_URLS: List[str] = [
        "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services",
        "https://learn.microsoft.com/en-us/azure/azure-government/compare-azure-government-global-azure",
        "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services-compute",
        "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services-networking",
        "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services-database",
        "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services-storage",
        "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services-securityandidentity",
        "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services-aiandcognitive",
        "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services-iot",
    ]

    CHINA_PARITY_URLS: List[str] = [
        "https://learn.microsoft.com/en-us/azure/china/resources-developer-guide",
        "https://learn.microsoft.com/en-us/azure/china/resources-azure-china-general-faq",
    ]

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "MicrosoftLearnMCPClient":
        self._client = httpx.AsyncClient(
            headers=self.DEFAULT_HEADERS,
            timeout=self._timeout,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch_page(self, url: str) -> str:
        """Fetch raw HTML content from a Microsoft Learn URL."""
        assert self._client is not None, "Use as async context manager."
        logger.debug(f"Fetching {url}")
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as exc:
            logger.warning(f"HTTP {exc.response.status_code} fetching {url}")
            return ""
        except httpx.RequestError as exc:
            logger.error(f"Request error fetching {url}: {exc}")
            return ""

    async def fetch_government_parity_pages(self) -> Dict[str, str]:
        """Fetch all Azure Government parity documentation pages."""
        tasks = {url: self.fetch_page(url) for url in self.GOVERNMENT_PARITY_URLS}
        results: Dict[str, str] = {}
        for url, coro in tasks.items():
            html = await coro
            if html:
                results[url] = html
            await asyncio.sleep(settings.scrape_delay_seconds)
        return results

    async def fetch_china_parity_pages(self) -> Dict[str, str]:
        """Fetch Azure China parity documentation pages."""
        results: Dict[str, str] = {}
        for url in self.CHINA_PARITY_URLS:
            html = await self.fetch_page(url)
            if html:
                results[url] = html
            await asyncio.sleep(settings.scrape_delay_seconds)
        return results

    async def search_docs(self, query: str, max_results: int = 10) -> List[Dict[str, str]]:
        """
        Search Microsoft Learn documentation.
        Uses the MS Learn search API; falls back to empty list on failure.
        """
        assert self._client is not None, "Use as async context manager."
        search_url = "https://learn.microsoft.com/api/search"
        params = {
            "search": query,
            "locale": "en-us",
            "$top": max_results,
            "facet": "category",
            "category": "Documentation",
        }
        try:
            response = await self._client.get(search_url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception as exc:
            logger.warning(f"Search failed for '{query}': {exc}")
            return []
