"""Feature extractor agent.

Uses an LLM to parse raw HTML content into structured FeatureRecord objects.
The agent constructs targeted prompts, sends them to Azure OpenAI, and
validates responses against the Pydantic schema.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from loguru import logger
from openai import AsyncAzureOpenAI

from config.settings import settings
from models.feature import CloudEnvironment, FeatureRecord, FeatureStatus
from utils.helpers import build_feature_id, parse_status_string

# Mapping of URL path fragments to cloud environments for heuristic tagging
URL_CLOUD_HINTS: Dict[str, List[CloudEnvironment]] = {
    "azure-government": [
        CloudEnvironment.GCC,
        CloudEnvironment.GCC_HIGH,
        CloudEnvironment.DOD_IL2,
        CloudEnvironment.DOD_IL4,
        CloudEnvironment.DOD_IL5,
    ],
    "china": [CloudEnvironment.CHINA],
}

EXTRACTION_SYSTEM_PROMPT = """\
You are an expert at extracting structured feature availability data from Azure cloud documentation.

Given a page of documentation HTML/text, extract a JSON array of feature records.
Each record must have:
  - service_name: Azure service name (string)
  - feature_name: Specific feature or capability (string)
  - category: Service category such as Compute, Networking, Storage, AI, Security, etc. (string)
  - description: Brief description (string or null)
  - status: an object mapping cloud environment keys to status values
    Cloud environment keys: commercial, gcc, gcc_high, dod_il2, dod_il4, dod_il5, china, germany
    Status values: "ga", "preview", "not_available", "unknown"
  - source_url: the source URL (string)
  - notes: any caveats or extra info (string or null)

Return ONLY valid JSON – an array of objects. No markdown, no explanation.
If you cannot find any feature records, return an empty array [].
"""


class FeatureExtractorAgent:
    """
    LLM-powered agent that converts raw documentation text into structured
    FeatureRecord objects.
    """

    def __init__(self) -> None:
        self._llm: Optional[AsyncAzureOpenAI] = None
        if settings.azure_openai_endpoint and settings.azure_openai_api_key:
            self._llm = AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
            )

    async def run(self, pages: Dict[str, str]) -> List[FeatureRecord]:
        """
        Extract FeatureRecord objects from a mapping of URL → raw HTML.

        Falls back to a heuristic HTML table parser when no LLM is configured.
        """
        all_records: List[FeatureRecord] = []
        for url, html in pages.items():
            logger.info(f"FeatureExtractorAgent: extracting from {url}")
            if self._llm:
                records = await self._extract_with_llm(url, html)
            else:
                records = self._extract_heuristic(url, html)
            all_records.extend(records)
            logger.info(f"  → extracted {len(records)} records")

        logger.success(f"FeatureExtractorAgent: total {len(all_records)} records extracted.")
        return all_records

    # ── LLM extraction ────────────────────────────────────────────────────────

    async def _extract_with_llm(self, url: str, html: str) -> List[FeatureRecord]:
        """Use Azure OpenAI to extract features from the page content."""
        # Truncate to ~12 k chars to stay within context budget
        snippet = self._clean_html(html)[:12_000]
        user_message = f"Source URL: {url}\n\nContent:\n{snippet}"

        try:
            response = await self._llm.chat.completions.create(
                model=settings.azure_openai_deployment,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.0,
                max_tokens=4096,
            )
            raw_json = response.choices[0].message.content or "[]"
            return self._parse_llm_response(raw_json, url)
        except Exception as exc:
            logger.error(f"LLM extraction failed for {url}: {exc}")
            return []

    def _parse_llm_response(self, raw_json: str, source_url: str) -> List[FeatureRecord]:
        """Parse and validate the LLM JSON output into FeatureRecord objects."""
        try:
            # Strip markdown code fences if present
            raw_json = re.sub(r"```(?:json)?", "", raw_json).strip()
            items = json.loads(raw_json)
            records: List[FeatureRecord] = []
            for item in items:
                item["id"] = build_feature_id(item.get("service_name", ""), item.get("feature_name", ""))
                item.setdefault("source_url", source_url)
                # Normalise status strings
                raw_status = item.get("status", {})
                item["status"] = {
                    env: (
                        raw_status.get(env.value, FeatureStatus.UNKNOWN)
                        if isinstance(raw_status.get(env.value), str)
                        and raw_status.get(env.value) in FeatureStatus.__members__.values()
                        else FeatureStatus.UNKNOWN
                    )
                    for env in CloudEnvironment
                }
                records.append(FeatureRecord(**item))
            return records
        except Exception as exc:
            logger.warning(f"Failed to parse LLM response: {exc}")
            return []

    # ── Heuristic extraction ──────────────────────────────────────────────────

    def _extract_heuristic(self, url: str, html: str) -> List[FeatureRecord]:
        """
        Simple regex / BeautifulSoup table parser when no LLM is available.
        Looks for Markdown-style tables with 'Available' / 'Yes' / 'Preview' keywords.
        """
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError:
            logger.debug("beautifulsoup4 not installed; skipping heuristic extraction.")
            return []

        cloud_hints = self._hints_from_url(url)
        soup = BeautifulSoup(html, "html.parser")
        records: List[FeatureRecord] = []

        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if not headers:
                continue
            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells or len(cells) < 2:
                    continue
                feature_name = cells[0]
                if not feature_name:
                    continue
                status_text = cells[1] if len(cells) > 1 else "unknown"
                status = parse_status_string(status_text)

                record_status = {env: FeatureStatus.UNKNOWN for env in CloudEnvironment}
                record_status[CloudEnvironment.COMMERCIAL] = FeatureStatus.GA  # assumed baseline
                for env in cloud_hints:
                    record_status[env] = status

                feature_id = build_feature_id("azure", feature_name)
                record = FeatureRecord(
                    id=feature_id,
                    service_name="Azure",
                    feature_name=feature_name,
                    category="General",
                    status=record_status,
                    source_url=url,
                )
                records.append(record)

        return records

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_html(html: str) -> str:
        """Strip HTML tags to produce readable text for the LLM."""
        try:
            from bs4 import BeautifulSoup  # type: ignore

            return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
        except ImportError:
            return re.sub(r"<[^>]+>", " ", html)

    @staticmethod
    def _hints_from_url(url: str) -> List[CloudEnvironment]:
        for fragment, envs in URL_CLOUD_HINTS.items():
            if fragment in url:
                return envs
        return []
