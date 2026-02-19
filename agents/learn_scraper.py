"""Microsoft Learn scraper agent.

Fetches Azure cloud parity documentation from Microsoft Learn and
returns raw HTML keyed by URL.
"""

from __future__ import annotations

from typing import Dict

from loguru import logger

from clients.ms_learn_client import MicrosoftLearnMCPClient


class LearnScraperAgent:
    """
    Responsible for fetching Azure cloud parity pages from Microsoft Learn.

    Uses MicrosoftLearnMCPClient under the hood, which supports the
    Microsoft Learn MCP server when available and falls back to direct
    HTTPS requests otherwise.
    """

    def __init__(self) -> None:
        self._client = MicrosoftLearnMCPClient()

    async def run(self, include_china: bool = True) -> Dict[str, str]:
        """
        Scrape all relevant Microsoft Learn parity pages.

        Returns:
            Mapping of URL → raw HTML string.
        """
        logger.info("LearnScraperAgent: starting scrape of Microsoft Learn parity docs...")
        results: Dict[str, str] = {}

        async with self._client as client:
            gov_pages = await client.fetch_government_parity_pages()
            results.update(gov_pages)
            logger.info(f"LearnScraperAgent: fetched {len(gov_pages)} government parity pages.")

            if include_china:
                china_pages = await client.fetch_china_parity_pages()
                results.update(china_pages)
                logger.info(f"LearnScraperAgent: fetched {len(china_pages)} China parity pages.")

        logger.success(f"LearnScraperAgent: done. Total pages fetched: {len(results)}")
        return results

    async def search(self, query: str, max_results: int = 10) -> list:
        """Search Microsoft Learn docs for a given query."""
        async with self._client as client:
            return await client.search_docs(query, max_results)
