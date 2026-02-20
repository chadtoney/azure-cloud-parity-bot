"""
Foundry-backed workflow executors for the Azure Cloud Feature Parity pipeline.

Each LLM-dependent executor wraps a Foundry ChatAgent created via
``AzureAIClient`` and is wired into a sequential chain via
``WorkflowBuilder``.

Pipeline
--------
StarterExecutor → ResearchExecutor → ExtractorExecutor
→ ComparisonExecutor → ReportExecutor

Agents created here are registered in the Azure AI Foundry project and
visible in the ai.azure.com portal.
"""

# NOTE: Do NOT add ``from __future__ import annotations`` – it breaks the
# agent_framework @handler decorator's runtime type-inspection.

import re
from uuid import uuid4

from agent_framework import (
    AgentRunResponseUpdate,
    AgentRunUpdateEvent,
    ChatAgent,
    ChatMessage,
    Executor,
    Role,
    TextContent,
    WorkflowContext,
    handler,
)
from loguru import logger

from agents.tools import (
    build_parity_report,
    build_report_markdown,
    clean_html,
    detect_changes,
    fetch_all_pages,
    get_response_text,
    parse_feature_records,
)
from config.settings import settings
from models.feature import CloudEnvironment
from storage.feature_store import FeatureStore


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent instruction prompts (shared with workflow.py for agent creation)
# ═══════════════════════════════════════════════════════════════════════════════

RESEARCH_INSTRUCTIONS = (
    "You are a research specialist that gathers and summarises Azure cloud "
    "parity documentation. When given information about fetched documentation "
    "pages, provide a concise summary of the coverage and any gaps you notice."
)

EXTRACTION_INSTRUCTIONS = """\
You are an expert at extracting structured feature availability data from \
Azure cloud documentation.

Given documentation text, extract a JSON array of feature records.
Each record MUST have these fields:
  - service_name: Azure service name (string)
  - feature_name: Specific feature or capability (string)
  - category: Service category (Compute, Networking, Storage, AI, Security, …)
  - description: Brief description (string or null)
  - status: object mapping cloud keys to status values
    Keys: commercial, gcc, gcc_high, dod_il2, dod_il4, dod_il5, china, germany
    Values: "ga", "preview", "not_available", "unknown"
  - source_url: the source URL (string)
  - notes: any caveats (string or null)

If no documentation is provided, generate records from your training \
knowledge.  Be as accurate as possible.  Return at least 10 records when \
generating from knowledge.

Return ONLY valid JSON — an array of objects.  No markdown fences, no \
explanation.  If you cannot find any features, return [].\
"""

REPORT_INSTRUCTIONS = """\
You are an Azure cloud solutions architect writing executive summaries of \
cloud feature parity reports.

Summarise the provided parity data clearly and concisely:
- Highlight the most significant gaps (features GA in Commercial but NOT \
available in sovereign clouds)
- Note features in Preview that may become GA soon
- Provide actionable recommendations for sovereign cloud customers

Keep the summary to 3-5 paragraphs.  Use plain English.  Do not use bullet \
points.\
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  1. Starter executor – parses user message, seeds pipeline state
# ═══════════════════════════════════════════════════════════════════════════════


class StarterExecutor(Executor):
    """Entry-point: parses user message and seeds the pipeline data dict."""

    def __init__(self):
        super().__init__(id="starter")

    @handler
    async def handle(
        self, messages: list[ChatMessage], ctx: WorkflowContext[dict]
    ) -> None:
        user_text = " ".join(
            part.text
            for msg in messages
            for part in (msg.contents or [])
            if hasattr(part, "text")
        ).strip() or "Run full parity analysis"

        service_match = re.search(
            r"(?:for|check|analyze|scan)\s+"
            r"([A-Za-z][A-Za-z0-9\s\-]+?)"
            r"(?:\s+service|\s+features?|$)",
            user_text,
            re.IGNORECASE,
        )
        target = service_match.group(1).strip() if service_match else None

        label = (
            f"Analyzing **{target}** cloud parity..."
            if target
            else "Running full Azure cloud parity analysis..."
        )
        await ctx.add_event(
            AgentRunUpdateEvent(
                self.id,
                data=AgentRunResponseUpdate(
                    contents=[TextContent(text=label)],
                    role=Role.ASSISTANT,
                    response_id=str(uuid4()),
                ),
            )
        )

        logger.info(f"StarterExecutor: query={user_text!r}, target={target}")
        await ctx.send_message({"query": user_text, "target_service": target})


# ═══════════════════════════════════════════════════════════════════════════════
#  2. Research executor – fetches Azure parity docs
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchExecutor(Executor):
    """Fetches Azure parity docs; uses a Foundry ChatAgent for analysis."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent):
        self.agent = agent
        super().__init__(id="research")

    @handler
    async def handle(self, data: dict, ctx: WorkflowContext[dict]) -> None:
        target = data.get("target_service")

        await ctx.add_event(
            AgentRunUpdateEvent(
                self.id,
                data=AgentRunResponseUpdate(
                    contents=[TextContent(text="Fetching Azure parity documentation...")],
                    role=Role.ASSISTANT,
                    response_id=str(uuid4()),
                ),
            )
        )

        # Direct Python call – heavy I/O stays out of the LLM
        pages = await fetch_all_pages(target_service=target)

        # Ask the Foundry agent to summarise what was found
        if pages:
            url_list = ", ".join(list(pages.keys())[:10])
            more = f" (and {len(pages) - 10} more)" if len(pages) > 10 else ""
            prompt = (
                f"I fetched {len(pages)} Azure documentation pages covering: "
                f"{url_list}{more}.  Briefly summarize the coverage."
            )
            try:
                response = await self.agent.run(
                    [ChatMessage(role=Role.USER, text=prompt)]
                )
                summary_text = get_response_text(response) or f"Fetched {len(pages)} pages."
            except Exception as exc:
                logger.warning(f"Research agent summary failed: {exc}")
                summary_text = f"Fetched {len(pages)} pages."
        else:
            summary_text = (
                "No pages fetched (scraping disabled or network unavailable). "
                "Will use LLM knowledge base."
            )

        await ctx.add_event(
            AgentRunUpdateEvent(
                self.id,
                data=AgentRunResponseUpdate(
                    contents=[TextContent(text=summary_text)],
                    role=Role.ASSISTANT,
                    response_id=str(uuid4()),
                ),
            )
        )

        data["scraped_pages"] = pages
        logger.info(f"ResearchExecutor: {len(pages)} pages.")
        await ctx.send_message(data)


# ═══════════════════════════════════════════════════════════════════════════════
#  3. Feature extractor executor – LLM extraction via Foundry ChatAgent
# ═══════════════════════════════════════════════════════════════════════════════


class ExtractorExecutor(Executor):
    """Extracts structured feature records using a Foundry ChatAgent."""

    agent: ChatAgent
    store: FeatureStore

    def __init__(self, agent: ChatAgent, store: FeatureStore = None):
        self.agent = agent
        self.store = store or FeatureStore()
        super().__init__(id="extractor")

    @handler
    async def handle(self, data: dict, ctx: WorkflowContext[dict]) -> None:
        pages = data.get("scraped_pages", {})
        query = data.get("query", "Azure cloud feature parity")

        if pages:
            status_msg = f"Extracting features from {len(pages)} pages..."
        else:
            status_msg = "Generating parity data from LLM knowledge..."

        await ctx.add_event(
            AgentRunUpdateEvent(
                self.id,
                data=AgentRunResponseUpdate(
                    contents=[TextContent(text=status_msg)],
                    role=Role.ASSISTANT,
                    response_id=str(uuid4()),
                ),
            )
        )

        all_records = []

        # ── Extract from scraped pages ───────────────────────────────
        if pages:
            for url, html in pages.items():
                snippet = clean_html(html)[:12_000]
                try:
                    response = await self.agent.run(
                        [ChatMessage(
                            role=Role.USER,
                            text=f"Source URL: {url}\n\nContent:\n{snippet}",
                        )]
                    )
                    raw = get_response_text(response) or "[]"
                    records = parse_feature_records(raw, url)
                    all_records.extend(records)
                    logger.info(f"  Extracted {len(records)} records from {url}")
                except Exception as exc:
                    logger.warning(f"Extraction failed for {url}: {exc}")

        # ── Fallback chain: store → knowledge ────────────────────────
        if not all_records:
            all_records = self.store.get_all()
            if all_records:
                logger.info(f"Loaded {len(all_records)} records from store.")
            else:
                logger.warning("Store empty — generating from LLM knowledge.")
                try:
                    response = await self.agent.run(
                        [ChatMessage(
                            role=Role.USER,
                            text=f"Generate feature parity data for: {query}",
                        )]
                    )
                    raw = get_response_text(response) or "[]"
                    all_records = parse_feature_records(raw)
                except Exception as exc:
                    logger.error(f"Knowledge generation failed: {exc}")

        if all_records:
            self.store.upsert_many(all_records)

        source = "scraped docs" if pages else "LLM knowledge"
        await ctx.add_event(
            AgentRunUpdateEvent(
                self.id,
                data=AgentRunResponseUpdate(
                    contents=[TextContent(
                        text=f"{len(all_records)} feature records ready (from {source})."
                    )],
                    role=Role.ASSISTANT,
                    response_id=str(uuid4()),
                ),
            )
        )

        data["feature_records"] = all_records
        logger.info(f"ExtractorExecutor: {len(all_records)} records.")
        await ctx.send_message(data)


# ═══════════════════════════════════════════════════════════════════════════════
#  4. Comparison executor – pure Python cross-cloud comparison
# ═══════════════════════════════════════════════════════════════════════════════


class ComparisonExecutor(Executor):
    """Builds cross-cloud comparison data from extracted records."""

    store: FeatureStore

    def __init__(self, store: FeatureStore = None):
        self.store = store or FeatureStore()
        super().__init__(id="comparison")

    @handler
    async def handle(self, data: dict, ctx: WorkflowContext[dict]) -> None:
        records = data.get("feature_records", [])

        await ctx.add_event(
            AgentRunUpdateEvent(
                self.id,
                data=AgentRunResponseUpdate(
                    contents=[TextContent(
                        text=(
                            f"Comparing {len(records)} features across "
                            "Commercial, GCC, GCC-High, DoD, and China..."
                        )
                    )],
                    role=Role.ASSISTANT,
                    response_id=str(uuid4()),
                ),
            )
        )

        report = build_parity_report(records, baseline=CloudEnvironment.COMMERCIAL)

        # Check for changes vs previous run
        previous = self.store.load_latest_report()
        if previous:
            changes = detect_changes(previous, report)
            if changes["new_gaps"]:
                logger.warning(f"  {len(changes['new_gaps'])} new parity gaps!")
            if changes["resolved_gaps"]:
                logger.info(f"  {len(changes['resolved_gaps'])} gaps resolved.")

        data["report"] = report
        logger.info("ComparisonExecutor: report built.")
        await ctx.send_message(data)


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Report executor – generates final Markdown via Foundry ChatAgent
# ═══════════════════════════════════════════════════════════════════════════════


class ReportExecutor(Executor):
    """Generates the final Markdown report using a Foundry ChatAgent."""

    agent: ChatAgent
    store: FeatureStore

    def __init__(self, agent: ChatAgent, store: FeatureStore = None):
        self.agent = agent
        self.store = store or FeatureStore()
        super().__init__(id="reporter")

    @handler
    async def handle(self, data: dict, ctx: WorkflowContext) -> None:
        report = data.get("report")

        if not report:
            markdown = (
                "No feature data available.  Please check your Foundry project "
                "endpoint and model deployment configuration."
            )
        else:
            markdown = build_report_markdown(report)

            # ── LLM executive summary via Foundry agent ──────────────
            stats = "\n".join(
                f"- {comp.target_cloud.value}: {comp.parity_percentage}% parity, "
                f"{len(comp.not_available_in_target)} gaps"
                for comp in report.comparisons.values()
            )
            prompt = (
                f"Total features: {report.total_features}\n\n"
                f"Parity by cloud:\n{stats}"
            )
            try:
                response = await self.agent.run(
                    [ChatMessage(role=Role.USER, text=prompt)]
                )
                summary = get_response_text(response)
                if summary:
                    report.summary = summary
                    markdown = (
                        f"## Executive Summary\n\n{summary}\n\n---\n\n{markdown}"
                    )
            except Exception as exc:
                logger.warning(f"LLM summary failed: {exc}")

            # ── Persist ──────────────────────────────────────────────
            path = self.store.save_report(report)
            md_path = path.with_suffix(".md")
            md_path.write_text(markdown, encoding="utf-8")
            logger.success(f"Report saved to {md_path}")

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
