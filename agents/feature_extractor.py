"""Feature extractor agent.

Uses an LLM to parse raw HTML content into structured FeatureRecord objects.
The agent constructs targeted prompts, sends them to Azure OpenAI, and
validates responses against the Pydantic schema.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
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

# Used when live scraping is unavailable – the LLM generates records from training knowledge.
KNOWLEDGE_SYSTEM_PROMPT = """\
You are an expert on Azure cloud feature availability across different Azure cloud environments.
Using your training knowledge, generate a JSON array of feature availability records for the
requested Azure service (or the most common Azure services if no specific service is named).

Each record must have:
  - service_name: Azure service name (string)
  - feature_name: Specific feature or capability (string)
  - category: Service category such as Compute, Networking, Storage, AI, Security, etc. (string)
  - description: Brief description (string or null)
  - status: an object mapping cloud environment keys to status values
    Cloud environment keys: commercial, gcc, gcc_high, dod_il2, dod_il4, dod_il5, china, germany
    Status values: "ga", "preview", "not_available", "unknown"
  - source_url: use "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services"
  - notes: any caveats or extra info (string or null)

Be as accurate as possible based on your training knowledge. Return at least 10 records.
Return ONLY valid JSON – an array of objects. No markdown, no explanation.
"""


class FeatureExtractorAgent:
    """
    LLM-powered agent that converts raw documentation text into structured
    FeatureRecord objects.
    """

    def __init__(self) -> None:
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

    async def run_from_knowledge(self, query: str) -> List[FeatureRecord]:
        """
        Generate FeatureRecord objects directly from the LLM's training knowledge.

        Used as a fallback when live scraping is unavailable (e.g. outbound network
        is blocked inside a Foundry hosted-agent container).
        """
        if not self._llm:
            logger.warning("FeatureExtractorAgent.run_from_knowledge: no LLM configured, returning [].")
            return []

        logger.info(f"FeatureExtractorAgent: generating records from LLM knowledge for query='{query}'")
        fallback_url = "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services"
        try:
            response = await self._llm.chat.completions.create(
                model=settings.azure_openai_deployment,
                messages=[
                    {"role": "system", "content": KNOWLEDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Query: {query}"},
                ],
                temperature=0.0,
                max_tokens=4096,
            )
            raw_json = response.choices[0].message.content or "[]"
            records = self._parse_llm_response(raw_json, fallback_url)
            logger.success(f"FeatureExtractorAgent: generated {len(records)} records from knowledge.")
            return records
        except Exception as exc:
            logger.error(f"FeatureExtractorAgent.run_from_knowledge failed: {exc}")
            return []

    async def run_direct_report(self, query: str) -> str:
        """
        Generate a complete formatted parity report in a SINGLE LLM call.

        Skips JSON extraction, Pydantic parsing, comparison loops, and the
        second LLM summary call in ReportGeneratorAgent. Use this in
        SKIP_SCRAPING mode where every millisecond counts.

        Returns:
            Full Markdown report string, or empty string on failure.
        """
        if not self._llm:
            logger.warning("FeatureExtractorAgent.run_direct_report: no LLM configured.")
            return ""

        logger.info(f"FeatureExtractorAgent: generating direct report for query='{query}'")
        system_prompt = """\
You are an expert Azure cloud architect. Using your training knowledge, produce a complete
Azure cloud feature parity report in Markdown for the user's query.

The report must include:
1. A brief executive summary (2-3 sentences)
2. A Markdown table with columns: Feature | Commercial | GCC | GCC-High | DoD IL2 | DoD IL4 | DoD IL5 | China
   - Use: GA / Preview / Not Available / Unknown for each cell
3. Key gaps and recommendations (3-5 bullet points)

Be specific and accurate. Focus on features relevant to the query.
Return ONLY Markdown — no preamble, no code fences.
"""
        try:
            response = await self._llm.chat.completions.create(
                model=settings.azure_openai_deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                temperature=0.0,
                max_tokens=2000,
            )
            report_md = response.choices[0].message.content or ""
            logger.success("FeatureExtractorAgent: direct report generated.")
            return report_md
        except Exception as exc:
            logger.error(f"FeatureExtractorAgent.run_direct_report failed: {exc}")
            return ""

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
