"""Microsoft Learn scraper agent.

Fetches Azure cloud parity documentation from Microsoft Learn and
returns raw HTML keyed by URL.
"""

from __future__ import annotations

import asyncio
from typing import Dict

from loguru import logger

from clients.ms_learn_client import MicrosoftLearnMCPClient

# Total time budget for the entire scrape phase.
# If outbound internet is blocked (e.g. in a Foundry container) each 8 s
# per-request timeout still sums up; this hard cap keeps the stage fast.
_SCRAPE_TIMEOUT_SECS = 20


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
            Mapping of URL → raw HTML string, or {} if the network is unreachable.
        """
        logger.info("LearnScraperAgent: starting scrape of Microsoft Learn parity docs...")
        try:
            results = await asyncio.wait_for(
                self._scrape(include_china), timeout=_SCRAPE_TIMEOUT_SECS
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"LearnScraperAgent: timed out after {_SCRAPE_TIMEOUT_SECS}s "
                "(outbound network may be restricted). Proceeding with empty pages."
            )
            results = {}
        logger.info(f"LearnScraperAgent: done. Total pages fetched: {len(results)}")
        return results

    async def _scrape(self, include_china: bool) -> Dict[str, str]:
        results: Dict[str, str] = {}
        async with self._client as client:
            gov_pages = await client.fetch_government_parity_pages()
            results.update(gov_pages)
            logger.info(f"LearnScraperAgent: fetched {len(gov_pages)} government parity pages.")

            if include_china:
                china_pages = await client.fetch_china_parity_pages()
                results.update(china_pages)
                logger.info(f"LearnScraperAgent: fetched {len(china_pages)} China parity pages.")
        return results

    async def search(self, query: str, max_results: int = 10) -> list:
        """Search Microsoft Learn docs for a given query."""
        try:
            async with self._client as client:
                return await asyncio.wait_for(
                    client.search_docs(query, max_results), timeout=8
                )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning(f"LearnScraperAgent.search failed: {exc}")
            return []
