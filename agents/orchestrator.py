"""Orchestrator agent.

Coordinates all other agents to run a complete feature parity analysis
pipeline: scrape → extract → compare → report.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger

from agents.comparison_agent import ComparisonAgent
from agents.feature_extractor import FeatureExtractorAgent
from agents.learn_scraper import LearnScraperAgent
from agents.report_generator import ReportGeneratorAgent
from agents.web_scraper import WebScraperAgent
from models.feature import CloudEnvironment, FeatureRecord, ParityReport
from storage.feature_store import FeatureStore


class OrchestratorAgent:
    """
    Top-level agent that orchestrates the full parity analysis pipeline.

    Pipeline steps:
    1. LearnScraperAgent   – fetch Microsoft Learn parity docs
    2. WebScraperAgent     – fetch Azure Updates + sovereign cloud pages
    3. FeatureExtractorAgent – parse HTML into FeatureRecord objects
    4. ComparisonAgent     – build cross-cloud FeatureComparison data
    5. ReportGeneratorAgent – produce a human-readable Markdown report
    """

    def __init__(
        self,
        store: Optional[FeatureStore] = None,
        baseline: CloudEnvironment = CloudEnvironment.COMMERCIAL,
    ) -> None:
        self._store = store or FeatureStore()
        self._baseline = baseline

        self._learn_scraper = LearnScraperAgent()
        self._web_scraper = WebScraperAgent()
        self._extractor = FeatureExtractorAgent()
        self._comparison = ComparisonAgent()
        self._reporter = ReportGeneratorAgent(store=self._store)

    async def run(
        self,
        extra_urls: Optional[List[str]] = None,
        skip_web_scrape: bool = False,
    ) -> ParityReport:
        """
        Execute the full parity analysis pipeline.

        Args:
            extra_urls: Additional URLs to scrape in the web scraping step.
            skip_web_scrape: Set True to only scrape Microsoft Learn (faster).

        Returns:
            Populated ParityReport persisted to disk.
        """
        logger.info("=" * 60)
        logger.info("OrchestratorAgent: starting full parity analysis pipeline")
        logger.info("=" * 60)

        # Step 1 – Microsoft Learn scrape
        logger.info("[1/5] Scraping Microsoft Learn parity docs...")
        learn_pages = await self._learn_scraper.run()

        # Step 2 – Web scrape
        web_pages: Dict[str, str] = {}
        if not skip_web_scrape:
            logger.info("[2/5] Scraping web sources...")
            web_pages = await self._web_scraper.run(extra_urls=extra_urls)
        else:
            logger.info("[2/5] Web scrape skipped.")

        all_pages = {**learn_pages, **web_pages}
        logger.info(f"Total pages to process: {len(all_pages)}")

        # Step 3 – Feature extraction
        logger.info("[3/5] Extracting features...")
        records: List[FeatureRecord] = await self._extractor.run(all_pages)

        if records:
            self._store.upsert_many(records)
            logger.info(f"Stored {len(records)} feature records.")
        else:
            # Fall back to existing store data if extraction returned nothing
            records = self._store.get_all()
            logger.warning(f"No new records extracted; using {len(records)} records from store.")

        # Step 4 – Comparison
        logger.info("[4/5] Comparing features across clouds...")
        report = self._comparison.run(records, baseline=self._baseline)

        # Detect changes vs previous report
        previous = self._store.load_latest_report()
        if previous:
            changes = self._comparison.detect_changes(previous, report)
            if changes["new_gaps"]:
                logger.warning(f"  {len(changes['new_gaps'])} new parity gaps detected!")
            if changes["resolved_gaps"]:
                logger.info(f"  {len(changes['resolved_gaps'])} gaps resolved since last run.")

        # Step 5 – Report generation
        logger.info("[5/5] Generating report...")
        await self._reporter.run(report)

        logger.info("=" * 60)
        logger.success("OrchestratorAgent: pipeline complete.")
        logger.info("=" * 60)
        return report

    async def run_targeted(self, service_name: str) -> ParityReport:
        """
        Run the pipeline scoped to a single Azure service by name.
        Searches Microsoft Learn for relevant docs then processes only those.
        """
        logger.info(f"OrchestratorAgent: targeted run for service='{service_name}'")
        search_results = await self._learn_scraper.search(
            f"Azure {service_name} government availability feature parity"
        )
        extra_urls = [r.get("url", "") for r in search_results if r.get("url")]
        return await self.run(extra_urls=extra_urls, skip_web_scrape=True)
