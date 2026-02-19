"""Comparison agent.

Compares feature availability across Azure cloud environments and
produces FeatureComparison and ParityReport objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from loguru import logger

from models.feature import (
    CloudEnvironment,
    FeatureComparison,
    FeatureRecord,
    FeatureStatus,
    ParityReport,
)


class ComparisonAgent:
    """
    Stateless agent that takes a list of FeatureRecords and produces
    comparisons between all cloud environments relative to a baseline.
    """

    def run(
        self,
        records: List[FeatureRecord],
        baseline: CloudEnvironment = CloudEnvironment.COMMERCIAL,
    ) -> ParityReport:
        """
        Build a full ParityReport comparing every non-baseline cloud to `baseline`.

        Args:
            records: All feature records to analyse.
            baseline: The reference cloud (default: Commercial).

        Returns:
            ParityReport with per-cloud FeatureComparison breakdowns.
        """
        logger.info(f"ComparisonAgent: comparing {len(records)} features against baseline={baseline.value}")
        comparisons: Dict[str, FeatureComparison] = {}

        target_clouds = [env for env in CloudEnvironment if env != baseline]
        for target in target_clouds:
            key = f"{baseline.value}_{target.value}"
            comparisons[key] = self._compare(records, baseline, target)

        report = ParityReport(
            generated_at=datetime.utcnow(),
            total_features=len(records),
            comparisons=comparisons,
        )
        logger.success("ComparisonAgent: report built.")
        return report

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _compare(
        self,
        records: List[FeatureRecord],
        baseline: CloudEnvironment,
        target: CloudEnvironment,
    ) -> FeatureComparison:
        comparison = FeatureComparison(
            baseline_cloud=baseline,
            target_cloud=target,
        )

        for record in records:
            b_status = record.get_status(baseline)
            t_status = record.get_status(target)

            if b_status != FeatureStatus.GA:
                continue  # only track features that are GA in baseline

            if t_status == FeatureStatus.GA:
                comparison.ga_in_both.append(record.id)
            elif t_status == FeatureStatus.PREVIEW:
                comparison.preview_in_target.append(record.id)
            elif t_status == FeatureStatus.NOT_AVAILABLE:
                comparison.not_available_in_target.append(record.id)
                comparison.ga_in_baseline_only.append(record.id)
            else:
                comparison.ga_in_baseline_only.append(record.id)

        logger.debug(
            f"  {baseline.value} → {target.value}: "
            f"parity={comparison.parity_percentage}% "
            f"gaps={len(comparison.not_available_in_target)}"
        )
        return comparison

    def detect_changes(
        self,
        previous: ParityReport,
        current: ParityReport,
    ) -> Dict[str, List[str]]:
        """
        Compare two reports and return newly added / removed gaps per cloud pair.

        Returns:
            Dict with keys 'new_gaps' and 'resolved_gaps', each a list of
            "<cloud_pair>/<feature_id>" strings.
        """
        new_gaps: List[str] = []
        resolved_gaps: List[str] = []

        all_keys = set(previous.comparisons) | set(current.comparisons)
        for key in all_keys:
            prev_comp = previous.comparisons.get(key)
            curr_comp = current.comparisons.get(key)

            prev_unavail = set(prev_comp.not_available_in_target if prev_comp else [])
            curr_unavail = set(curr_comp.not_available_in_target if curr_comp else [])

            for fid in curr_unavail - prev_unavail:
                new_gaps.append(f"{key}/{fid}")
            for fid in prev_unavail - curr_unavail:
                resolved_gaps.append(f"{key}/{fid}")

        return {"new_gaps": new_gaps, "resolved_gaps": resolved_gaps}
