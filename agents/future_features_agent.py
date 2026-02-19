"""Future features agent.

Analyses parity data to suggest upcoming Azure feature advancements for each
sovereign cloud, prioritising features currently in Preview (high-confidence
candidates for GA) and features with unknown status (lower-confidence).

An optional LLM call generates a plain-English roadmap narrative that is
stored in ``ParityReport.future_narrative``.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from loguru import logger
from openai import AsyncAzureOpenAI

from config.settings import settings
from models.feature import (
    CloudEnvironment,
    FeatureRecord,
    FeatureStatus,
    FutureSuggestion,
    ParityReport,
    SuggestionConfidence,
)

FUTURE_SYSTEM_PROMPT = """\
You are an Azure cloud roadmap analyst. Based on the Azure feature parity data provided,
write a concise future-features roadmap narrative (3-4 paragraphs, plain English, no bullet points).

Focus on:
- Features currently in Preview across sovereign clouds that are likely to reach GA soon
- Patterns suggesting which cloud environments are closest to parity with Azure Commercial
- Actionable guidance for customers planning workloads in sovereign clouds
- Any notable services where parity gaps are shrinking fastest

Keep the tone positive and forward-looking.
"""


class FutureFeaturesAgent:
    """
    Generates a list of :class:`FutureSuggestion` objects and an optional
    LLM-written roadmap narrative from a :class:`ParityReport`.
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
                token_provider = get_bearer_token_provider(
                    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
                )
                self._llm = AsyncAzureOpenAI(
                    azure_endpoint=settings.azure_openai_endpoint,
                    azure_ad_token_provider=token_provider,
                    api_version=settings.azure_openai_api_version,
                )

    async def run(
        self,
        report: ParityReport,
        records: List[FeatureRecord],
    ) -> ParityReport:
        """
        Populate ``report.future_suggestions`` and ``report.future_narrative``
        in-place, then return the updated report.

        Args:
            report: The parity report produced by :class:`ComparisonAgent`.
            records: All feature records used to build the report.

        Returns:
            The same ``report`` object with suggestions and narrative added.
        """
        logger.info("FutureFeaturesAgent: generating future feature suggestions...")

        record_map: Dict[str, FeatureRecord] = {r.id: r for r in records}
        suggestions = self._build_suggestions(report, record_map)
        report.future_suggestions = suggestions
        logger.info(f"  → {len(suggestions)} suggestions generated.")

        if self._llm:
            report.future_narrative = await self._llm_narrative(report)

        logger.success("FutureFeaturesAgent: done.")
        return report

    # ── Heuristic suggestion builder ─────────────────────────────────────────

    def _build_suggestions(
        self,
        report: ParityReport,
        record_map: Dict[str, FeatureRecord],
    ) -> List[FutureSuggestion]:
        suggestions: List[FutureSuggestion] = []

        for comp in report.comparisons.values():
            target = comp.target_cloud

            # Preview features → high confidence they will become GA
            for fid in comp.preview_in_target:
                record = record_map.get(fid)
                if record is None:
                    continue
                suggestions.append(
                    FutureSuggestion(
                        feature_id=fid,
                        service_name=record.service_name,
                        feature_name=record.feature_name,
                        target_cloud=target,
                        current_status=FeatureStatus.PREVIEW,
                        rationale=(
                            f"**{record.feature_name}** is currently in Preview in "
                            f"**{target.value}**. Preview features typically reach GA "
                            "within 6–12 months, making this a near-term advancement candidate."
                        ),
                        confidence=SuggestionConfidence.HIGH,
                        estimated_timeline="6–12 months",
                    )
                )

            # Features GA in baseline but with unknown status in target
            # (may be in development or simply undocumented)
            unknown_gap_ids = (
                set(comp.ga_in_baseline_only) - set(comp.not_available_in_target)
            )
            for fid in sorted(unknown_gap_ids):
                record = record_map.get(fid)
                if record is None:
                    continue
                suggestions.append(
                    FutureSuggestion(
                        feature_id=fid,
                        service_name=record.service_name,
                        feature_name=record.feature_name,
                        target_cloud=target,
                        current_status=FeatureStatus.UNKNOWN,
                        rationale=(
                            f"**{record.feature_name}** is GA in Azure Commercial but "
                            f"its status in **{target.value}** is undocumented. "
                            "Microsoft typically brings undocumented features to sovereign clouds "
                            "on a 12–18 month lag."
                        ),
                        confidence=SuggestionConfidence.LOW,
                        estimated_timeline="12–18 months",
                    )
                )

        return suggestions

    # ── LLM narrative ─────────────────────────────────────────────────────────

    async def _llm_narrative(self, report: ParityReport) -> str:
        """Ask the LLM for a forward-looking roadmap narrative."""
        preview_lines = [
            f"- {s.service_name} / {s.feature_name} → {s.target_cloud.value} "
            f"(confidence: {s.confidence.value})"
            for s in report.future_suggestions
            if s.current_status == FeatureStatus.PREVIEW
        ]
        unknown_lines = [
            f"- {s.service_name} / {s.feature_name} → {s.target_cloud.value}"
            for s in report.future_suggestions
            if s.current_status == FeatureStatus.UNKNOWN
        ]

        preview_block = "\n".join(preview_lines) if preview_lines else "None"
        unknown_block = "\n".join(unknown_lines[:20]) if unknown_lines else "None"  # cap for context

        prompt = (
            f"Total features tracked: {report.total_features}\n\n"
            f"Features in Preview (high-confidence near-term GA candidates):\n{preview_block}\n\n"
            f"Features with unknown sovereign status (lower-confidence):\n{unknown_block}"
        )

        try:
            response = await self._llm.chat.completions.create(
                model=settings.azure_openai_deployment,
                messages=[
                    {"role": "system", "content": FUTURE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=600,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning(f"FutureFeaturesAgent LLM narrative failed: {exc}")
            return ""
