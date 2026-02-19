"""Web scraper agent.

Fetches content from Azure Updates and other public Azure pages
that are not part of the Microsoft Learn documentation tree.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger

from clients.web_client import WebContentClient


class WebScraperAgent:
    """
    Scrapes Azure sovereign cloud pages, Azure Updates blog, and other
    non-Learn sources to supplement feature parity data.
    """

    def __init__(self) -> None:
        self._client = WebContentClient()

    async def run(self, extra_urls: Optional[List[str]] = None) -> Dict[str, str]:
        """
        Scrape all configured web sources.

        Args:
            extra_urls: Additional URLs to scrape beyond the defaults.

        Returns:
            Mapping of URL → raw HTML string.
        """

        logger.info("WebScraperAgent: starting web scrape...")
        results: Dict[str, str] = {}

        async with self._client as client:
            updates_html = await client.fetch_azure_updates()
            if updates_html:
                results[client.AZURE_UPDATES_URL] = updates_html
                logger.info("WebScraperAgent: fetched Azure Updates page.")

            sovereign_pages = await client.fetch_sovereign_docs()
            results.update(sovereign_pages)
            logger.info(f"WebScraperAgent: fetched {len(sovereign_pages)} sovereign cloud pages.")

            if extra_urls:
                extra_pages = await client.fetch_many(extra_urls)
                results.update(extra_pages)
                logger.info(f"WebScraperAgent: fetched {len(extra_pages)} extra pages.")

        logger.success(f"WebScraperAgent: done. Total pages: {len(results)}")
        return results
