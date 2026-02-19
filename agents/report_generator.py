"""Report generator agent.

Produces human-readable parity reports from ParityReport objects,
optionally using an LLM to generate a natural-language executive summary.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from loguru import logger
from openai import AsyncAzureOpenAI

from config.settings import settings
from models.feature import (
    CloudEnvironment,
    FeatureComparison,
    FeatureStatus,
    ParityReport,
    SuggestionConfidence,
)
from storage.feature_store import FeatureStore

SUMMARY_SYSTEM_PROMPT = """\
You are an Azure cloud solutions architect writing an executive summary of a cloud feature parity report.

Summarise the following parity data clearly and concisely:
- Highlight the most significant gaps (features GA in Commercial but NOT available in sovereign clouds)
- Note any features in Preview that may become GA soon
- Reference the future features roadmap where relevant
- Provide actionable recommendations for customers who need specific sovereign cloud support

Keep the summary to 3-5 paragraphs. Use plain English. Do not use bullet points.
"""


class ReportGeneratorAgent:
    """Generates Markdown and plain-text parity reports from ParityReport data."""

    def __init__(self, store: Optional[FeatureStore] = None) -> None:
        self._store = store or FeatureStore()
        self._llm: Optional[AsyncAzureOpenAI] = None
        if settings.azure_openai_endpoint:
            if settings.azure_openai_api_key:
                self._llm = AsyncAzureOpenAI(
                    azure_endpoint=settings.azure_openai_endpoint,
                    api_key=settings.azure_openai_api_key,
                    api_version=settings.azure_openai_api_version,
                )
            else:
                # Entra ID auth – uses az login / managed identity / workload identity
                token_provider = get_bearer_token_provider(
                    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
                )
                self._llm = AsyncAzureOpenAI(
                    azure_endpoint=settings.azure_openai_endpoint,
                    azure_ad_token_provider=token_provider,
                    api_version=settings.azure_openai_api_version,
                )

    async def run(self, report: ParityReport) -> str:
        """Build the full Markdown report and optionally attach an LLM summary."""
        logger.info("ReportGeneratorAgent: generating report...")

        md = self._build_markdown(report)

        if self._llm:
            summary = await self._llm_summary(report)
            report.summary = summary
            md = f"## Executive Summary\n\n{summary}\n\n---\n\n{md}"

        # Persist the updated report
        path = self._store.save_report(report)
        md_path = path.with_suffix(".md")
        md_path.write_text(md, encoding="utf-8")
        logger.success(f"ReportGeneratorAgent: report written to {md_path}")
        return md

    # ── Markdown builder ──────────────────────────────────────────────────────

    def _build_markdown(self, report: ParityReport) -> str:
        lines = [
            f"# Azure Cloud Feature Parity Report",
            f"",
            f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ",
            f"**Total features tracked:** {report.total_features}",
            f"",
            f"---",
            f"",
            f"## Parity Summary by Cloud",
            f"",
            f"| Cloud | Parity % | GA in Both | Preview | Not Available |",
            f"|-------|----------|------------|---------|---------------|",
        ]

        for key, comp in sorted(report.comparisons.items()):
            lines.append(
                f"| {comp.target_cloud.value} "
                f"| {comp.parity_percentage}% "
                f"| {len(comp.ga_in_both)} "
                f"| {len(comp.preview_in_target)} "
                f"| {len(comp.not_available_in_target)} |"
            )

        lines += ["", "---", "", "## Detailed Gaps by Cloud", ""]

        for key, comp in sorted(report.comparisons.items()):
            if not comp.not_available_in_target:
                continue
            lines += [
                f"### {comp.baseline_cloud.value} → {comp.target_cloud.value} gaps",
                f"",
                f"Features GA in **{comp.baseline_cloud.value}** but **not available** in **{comp.target_cloud.value}**:",
                f"",
            ]
            for fid in sorted(comp.not_available_in_target[:50]):  # cap at 50 for readability
                lines.append(f"- `{fid}`")
            if len(comp.not_available_in_target) > 50:
                lines.append(f"- *… and {len(comp.not_available_in_target) - 50} more*")
            lines.append("")

        lines += self._build_future_section(report)

        return "\n".join(lines)

    # ── Future features section ───────────────────────────────────────────────

    def _build_future_section(self, report: ParityReport) -> list:
        """Build the '## Future Features & Roadmap' Markdown section."""
        lines = ["", "---", "", "## Future Features & Roadmap", ""]

        if report.future_narrative:
            lines += [report.future_narrative, ""]

        if not report.future_suggestions:
            lines.append("*No future feature suggestions available for this report.*")
            lines.append("")
            return lines

        # Group suggestions by cloud and confidence
        by_cloud: dict = defaultdict(list)
        for s in report.future_suggestions:
            by_cloud[s.target_cloud.value].append(s)

        for cloud_key in sorted(by_cloud.keys()):
            suggestions = by_cloud[cloud_key]
            high = [s for s in suggestions if s.confidence == SuggestionConfidence.HIGH]
            medium = [s for s in suggestions if s.confidence == SuggestionConfidence.MEDIUM]
            low = [s for s in suggestions if s.confidence == SuggestionConfidence.LOW]

            lines += [f"### {cloud_key}", ""]

            if high:
                lines.append(
                    f"**Near-term (Preview → GA, ~{high[0].estimated_timeline or '6–12 months'})** "
                    f"— {len(high)} feature(s):"
                )
                for s in high[:20]:
                    lines.append(f"- `{s.feature_id}` — {s.service_name}")
                if len(high) > 20:
                    lines.append(f"- *… and {len(high) - 20} more*")
                lines.append("")

            if medium:
                lines.append(f"**Medium-term** — {len(medium)} feature(s):")
                for s in medium[:10]:
                    lines.append(f"- `{s.feature_id}` — {s.service_name}")
                if len(medium) > 10:
                    lines.append(f"- *… and {len(medium) - 10} more*")
                lines.append("")

            if low:
                lines.append(
                    f"**Longer-term / status unknown** — {len(low)} feature(s) "
                    "(status undocumented; advancement not guaranteed):"
                )
                for s in low[:10]:
                    lines.append(f"- `{s.feature_id}` — {s.service_name}")
                if len(low) > 10:
                    lines.append(f"- *… and {len(low) - 10} more*")
                lines.append("")

        return lines

    # ── LLM summary ───────────────────────────────────────────────────────────

    async def _llm_summary(self, report: ParityReport) -> str:
        """Ask the LLM for an executive summary of the parity report."""
        stats = "\n".join(
            f"- {comp.target_cloud.value}: {comp.parity_percentage}% parity, "
            f"{len(comp.not_available_in_target)} gaps"
            for comp in report.comparisons.values()
        )
        prompt = f"Total features: {report.total_features}\n\nParity by cloud:\n{stats}"
        try:
            response = await self._llm.chat.completions.create(
                model=settings.azure_openai_deployment,
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=800,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning(f"LLM summary failed: {exc}")
            return ""
