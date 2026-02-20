"""Pipeline helper functions for the Azure Cloud Feature Parity workflow.

Encapsulates scraping, feature-record parsing, comparison logic, and
Markdown report building used by the Foundry-backed executors.

These are pure Python functions — NOT agent-framework tools.  They run
inside executor code and keep the heavy data processing out of the LLM
context window.
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from clients.ms_learn_client import MicrosoftLearnMCPClient
from clients.web_client import WebContentClient
from config.settings import settings
from models.feature import (
    CloudEnvironment,
    FeatureComparison,
    FeatureRecord,
    FeatureStatus,
    ParityReport,
)
from utils.helpers import build_feature_id

_SCRAPE_TIMEOUT_SECS = 20


# ── Scraping ──────────────────────────────────────────────────────────────────


async def fetch_all_pages(
    target_service: Optional[str] = None,
    extra_urls: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Fetch all relevant Azure parity documentation pages.

    Returns a mapping of URL → raw HTML content.
    Returns ``{}`` when *SKIP_SCRAPING* is enabled or the network is
    unreachable.
    """
    if settings.skip_scraping:
        logger.info("Scraping disabled (SKIP_SCRAPING=true).")
        return {}

    pages: Dict[str, str] = {}
    extra: List[str] = list(extra_urls or [])

    # ── Microsoft Learn ──────────────────────────────────────────────
    try:
        async with MicrosoftLearnMCPClient() as client:
            gov = await asyncio.wait_for(
                client.fetch_government_parity_pages(),
                timeout=_SCRAPE_TIMEOUT_SECS,
            )
            pages.update(gov)
            logger.info(f"Fetched {len(gov)} government parity pages.")

            china = await asyncio.wait_for(
                client.fetch_china_parity_pages(),
                timeout=_SCRAPE_TIMEOUT_SECS,
            )
            pages.update(china)
            logger.info(f"Fetched {len(china)} China parity pages.")

            if target_service:
                results = await asyncio.wait_for(
                    client.search_docs(
                        f"Azure {target_service} government availability feature parity"
                    ),
                    timeout=8,
                )
                extra.extend(r.get("url", "") for r in results if r.get("url"))
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning(f"MS Learn scraping failed: {exc}")

    # ── Web sources ──────────────────────────────────────────────────
    try:
        async with WebContentClient() as client:
            updates = await asyncio.wait_for(
                client.fetch_azure_updates(),
                timeout=_SCRAPE_TIMEOUT_SECS,
            )
            if updates:
                pages[client.AZURE_UPDATES_URL] = updates

            sovereign = await asyncio.wait_for(
                client.fetch_sovereign_docs(),
                timeout=_SCRAPE_TIMEOUT_SECS,
            )
            pages.update(sovereign)

            if extra:
                extras = await asyncio.wait_for(
                    client.fetch_many(extra),
                    timeout=_SCRAPE_TIMEOUT_SECS,
                )
                pages.update(extras)
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning(f"Web scraping failed: {exc}")

    logger.info(f"Total pages fetched: {len(pages)}")
    return pages


# ── HTML helpers ──────────────────────────────────────────────────────────────


def clean_html(html: str) -> str:
    """Strip HTML tags to produce readable text for the LLM."""
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html)


# ── Feature record parsing ───────────────────────────────────────────────────


def parse_feature_records(
    raw_json: str, source_url: str = ""
) -> List[FeatureRecord]:
    """Parse JSON text (from LLM output) into validated FeatureRecord objects."""
    try:
        cleaned = re.sub(r"```(?:json)?", "", raw_json).strip()
        items = json.loads(cleaned)
        valid_statuses = {s.value for s in FeatureStatus}
        records: List[FeatureRecord] = []
        for item in items:
            item["id"] = build_feature_id(
                item.get("service_name", ""), item.get("feature_name", "")
            )
            item.setdefault("source_url", source_url)
            raw_status = item.get("status", {})
            item["status"] = {
                env: (
                    raw_status.get(env.value, FeatureStatus.UNKNOWN)
                    if isinstance(raw_status.get(env.value), str)
                    and raw_status.get(env.value) in valid_statuses
                    else FeatureStatus.UNKNOWN
                )
                for env in CloudEnvironment
            }
            records.append(FeatureRecord(**item))
        return records
    except Exception as exc:
        logger.warning(f"Failed to parse feature records: {exc}")
        return []


# ── Agent response helpers ────────────────────────────────────────────────────


def get_response_text(response) -> str:
    """Extract the assistant text from an agent run response.

    Works with both ``response.text`` (convenience property) and the
    underlying ``response.messages`` list.
    """
    if hasattr(response, "text") and response.text:
        return response.text
    for msg in getattr(response, "messages", []) or []:
        if hasattr(msg, "text") and msg.text:
            return msg.text
        for part in getattr(msg, "contents", []) or []:
            if hasattr(part, "text") and part.text:
                return part.text
    return ""


# ── Comparison logic ─────────────────────────────────────────────────────────


def build_parity_report(
    records: List[FeatureRecord],
    baseline: CloudEnvironment = CloudEnvironment.COMMERCIAL,
) -> ParityReport:
    """Build a ParityReport comparing all clouds against *baseline*."""
    comparisons: Dict[str, FeatureComparison] = {}
    targets = [env for env in CloudEnvironment if env != baseline]

    for target in targets:
        key = f"{baseline.value}_{target.value}"
        comp = FeatureComparison(baseline_cloud=baseline, target_cloud=target)
        for record in records:
            b_status = record.get_status(baseline)
            t_status = record.get_status(target)
            if b_status != FeatureStatus.GA:
                continue
            if t_status == FeatureStatus.GA:
                comp.ga_in_both.append(record.id)
            elif t_status == FeatureStatus.PREVIEW:
                comp.preview_in_target.append(record.id)
            elif t_status == FeatureStatus.NOT_AVAILABLE:
                comp.not_available_in_target.append(record.id)
                comp.ga_in_baseline_only.append(record.id)
            else:
                comp.ga_in_baseline_only.append(record.id)
        comparisons[key] = comp

    return ParityReport(
        generated_at=datetime.utcnow(),
        total_features=len(records),
        comparisons=comparisons,
    )


def build_report_markdown(report: ParityReport) -> str:
    """Build a full Markdown report from a ParityReport."""
    lines = [
        "# Azure Cloud Feature Parity Report",
        "",
        f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Total features tracked:** {report.total_features}",
        "",
        "---",
        "",
        "## Parity Summary by Cloud",
        "",
        "| Cloud | Parity % | GA in Both | Preview | Not Available |",
        "|-------|----------|------------|---------|---------------|",
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
            "",
            f"Features GA in **{comp.baseline_cloud.value}** but "
            f"**not available** in **{comp.target_cloud.value}**:",
            "",
        ]
        for fid in sorted(comp.not_available_in_target[:50]):
            lines.append(f"- `{fid}`")
        if len(comp.not_available_in_target) > 50:
            remaining = len(comp.not_available_in_target) - 50
            lines.append(f"- *… and {remaining} more*")
        lines.append("")

    return "\n".join(lines)


def detect_changes(
    previous: ParityReport, current: ParityReport
) -> Dict[str, List[str]]:
    """Compare two reports and return newly added / resolved gaps."""
    new_gaps: List[str] = []
    resolved_gaps: List[str] = []
    all_keys = set(previous.comparisons) | set(current.comparisons)

    for key in all_keys:
        prev_comp = previous.comparisons.get(key)
        curr_comp = current.comparisons.get(key)
        prev_set = set(prev_comp.not_available_in_target if prev_comp else [])
        curr_set = set(curr_comp.not_available_in_target if curr_comp else [])
        new_gaps.extend(f"{key}/{fid}" for fid in curr_set - prev_set)
        resolved_gaps.extend(f"{key}/{fid}" for fid in prev_set - curr_set)

    return {"new_gaps": new_gaps, "resolved_gaps": resolved_gaps}
