"""
Agent Framework executors for the Azure Cloud Feature Parity pipeline.

Each class inherits from Executor (Microsoft Agent Framework) and is wired
into a linear chain via WorkflowBuilder.add_chain.  Data flows between steps
through ctx.set_shared_state / ctx.get_shared_state.

Pipeline:
  ParityStarterExecutor  → LearnScraperExecutor → WebScraperExecutor
  → FeatureExtractorExecutor → ComparisonExecutor → ReportExecutor
"""

# NOTE: Do NOT add `from __future__ import annotations` – it breaks the
# agent_framework handler decorator's runtime type checks.

import re
from typing import Optional
from uuid import uuid4

from agent_framework import (
    AgentRunResponseUpdate,
    AgentRunUpdateEvent,
    ChatMessage,
    Executor,
    Role,
    TextContent,
    WorkflowContext,
    handler,
)
from loguru import logger

from agents.comparison_agent import ComparisonAgent
from agents.feature_extractor import FeatureExtractorAgent
from agents.learn_scraper import LearnScraperAgent
from agents.report_generator import ReportGeneratorAgent
from agents.web_scraper import WebScraperAgent
from models.feature import CloudEnvironment
from storage.feature_store import FeatureStore

# SharedState keys
KEY_QUERY = "user_query"
KEY_TARGET_SERVICE = "target_service"
KEY_EXTRA_URLS = "extra_urls"
KEY_SCRAPED_PAGES = "scraped_pages"
KEY_FEATURE_RECORDS = "feature_records"
KEY_REPORT = "report"
KEY_MARKDOWN = "markdown_report"


# ---------------------------------------------------------------------------
# 1. Starter executor – parses the user message and seeds shared state
# ---------------------------------------------------------------------------

class ParityStarterExecutor(Executor):
    """Entry-point executor – parses user message and seeds shared state."""

    def __init__(self) -> None:
        super().__init__(id="parity_starter")

    @handler
    async def start(
        self,
        messages: list[ChatMessage],
        ctx: WorkflowContext[dict],
    ) -> None:
        user_text = " ".join(
            part.text
            for msg in messages
            for part in (msg.contents or [])
            if hasattr(part, "text")
        ).strip() or "Run full parity analysis"

        await ctx.set_shared_state(KEY_QUERY, user_text)
        await ctx.set_shared_state(KEY_SCRAPED_PAGES, {})
        await ctx.set_shared_state(KEY_EXTRA_URLS, [])

        service_match = re.search(
            r"(?:for|check|analyze|scan)\s+([A-Za-z][A-Za-z0-9\s\-]+?)(?:\s+service|\s+features?|$)",
            user_text,
            re.IGNORECASE,
        )
        target = service_match.group(1).strip() if service_match else None
        await ctx.set_shared_state(KEY_TARGET_SERVICE, target)

        if target:
            logger.info(f"StarterExecutor: targeted service = '{target}'")
        else:
            logger.info("StarterExecutor: full parity scan requested")
        await ctx.send_message({})


# ---------------------------------------------------------------------------
# 2. Microsoft Learn scraper executor
# ---------------------------------------------------------------------------

class LearnScraperExecutor(Executor):
    """Fetches Azure parity documentation from Microsoft Learn."""

    def __init__(self) -> None:
        super().__init__(id="learn_scraper")
        self._agent = LearnScraperAgent()

    @handler
    async def scrape_learn(self, _prev: dict, ctx: WorkflowContext[dict]) -> None:
        target = await ctx.get_shared_state(KEY_TARGET_SERVICE)
        extra_urls: list = await ctx.get_shared_state(KEY_EXTRA_URLS) or []

        if target:
            results = await self._agent.search(
                f"Azure {target} government availability feature parity"
            )
            for r in results:
                url = r.get("url", "")
                if url:
                    extra_urls.append(url)
            await ctx.set_shared_state(KEY_EXTRA_URLS, extra_urls)

        pages = await self._agent.run()
        existing: dict = await ctx.get_shared_state(KEY_SCRAPED_PAGES) or {}
        await ctx.set_shared_state(KEY_SCRAPED_PAGES, {**existing, **pages})
        logger.info(f"LearnScraperExecutor: fetched {len(pages)} pages.")
        await ctx.send_message({})


# ---------------------------------------------------------------------------
# 3. Web scraper executor
# ---------------------------------------------------------------------------

class WebScraperExecutor(Executor):
    """Fetches Azure Updates and sovereign cloud pages from the open web."""

    def __init__(self) -> None:
        super().__init__(id="web_scraper")
        self._agent = WebScraperAgent()

    @handler
    async def scrape_web(self, _prev: dict, ctx: WorkflowContext[dict]) -> None:
        extra_urls: list = await ctx.get_shared_state(KEY_EXTRA_URLS) or []
        pages = await self._agent.run(extra_urls=extra_urls or None)
        existing: dict = await ctx.get_shared_state(KEY_SCRAPED_PAGES) or {}
        await ctx.set_shared_state(KEY_SCRAPED_PAGES, {**existing, **pages})
        logger.info(f"WebScraperExecutor: +{len(pages)} pages.")
        await ctx.send_message({})


# ---------------------------------------------------------------------------
# 4. Feature extractor executor
# ---------------------------------------------------------------------------

class FeatureExtractorExecutor(Executor):
    """Parses raw HTML into structured FeatureRecord objects using LLM."""

    def __init__(self, store: Optional[FeatureStore] = None) -> None:
        super().__init__(id="feature_extractor")
        self._agent = FeatureExtractorAgent()
        self._store = store or FeatureStore()

    @handler
    async def extract_features(self, _prev: dict, ctx: WorkflowContext[dict]) -> None:
        pages: dict = await ctx.get_shared_state(KEY_SCRAPED_PAGES) or {}
        records = await self._agent.run(pages)

        if records:
            self._store.upsert_many(records)
        else:
            records = self._store.get_all()
            logger.warning(f"FeatureExtractorExecutor: no new records; loaded {len(records)} from store.")

        await ctx.set_shared_state(KEY_FEATURE_RECORDS, records)
        logger.info(f"FeatureExtractorExecutor: {len(records)} records.")
        await ctx.send_message({})


# ---------------------------------------------------------------------------
# 5. Comparison executor
# ---------------------------------------------------------------------------

class ComparisonExecutor(Executor):
    """Builds cross-cloud FeatureComparison data from extracted records."""

    def __init__(self) -> None:
        super().__init__(id="comparison")
        self._agent = ComparisonAgent()

    @handler
    async def compare(self, _prev: dict, ctx: WorkflowContext[dict]) -> None:
        records = await ctx.get_shared_state(KEY_FEATURE_RECORDS) or []
        report = self._agent.run(records, baseline=CloudEnvironment.COMMERCIAL)
        await ctx.set_shared_state(KEY_REPORT, report)
        logger.info("ComparisonExecutor: report built.")
        await ctx.send_message({})


# ---------------------------------------------------------------------------
# 6. Report executor – final step; emits the result to the HTTP response
# ---------------------------------------------------------------------------

class ReportExecutor(Executor):
    """Generates a Markdown report and streams it back to the HTTP caller."""

    def __init__(self, store: Optional[FeatureStore] = None) -> None:
        super().__init__(id="report_generator")
        self._agent = ReportGeneratorAgent(store=store or FeatureStore())

    @handler
    async def generate_report(self, _prev: dict, ctx: WorkflowContext) -> None:
        report = await ctx.get_shared_state(KEY_REPORT)

        if not report:
            markdown = "No feature data available. Please retry after configuring Azure OpenAI credentials."
        else:
            markdown = await self._agent.run(report)

        await ctx.set_shared_state(KEY_MARKDOWN, markdown)

        await ctx.add_event(
            AgentRunUpdateEvent(
                self.id,
                data=AgentRunResponseUpdate(
                    contents=[TextContent(text=markdown)],
                    role=Role.ASSISTANT,
                    response_id=str(uuid4()),
                ),
            )
        )
        logger.success("ReportExecutor: response emitted.")
