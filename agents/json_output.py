"""JSON output agent.

Serialises the full parity analysis results – both the ParityReport summary
and all underlying FeatureRecords – to a structured JSON file and returns the
JSON string to the caller.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from loguru import logger

from config.settings import settings
from models.feature import FeatureRecord, ParityReport
from storage.feature_store import FeatureStore


class JsonOutputAgent:
    """Produces a structured JSON output file from a ParityReport and FeatureRecords."""

    def __init__(self, store: Optional[FeatureStore] = None) -> None:
        self._store = store or FeatureStore()
        self._reports_dir = Path(settings.reports_dir)
        self._reports_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        report: ParityReport,
        records: Optional[List[FeatureRecord]] = None,
        filename: Optional[str] = None,
    ) -> str:
        """Serialise report and feature records to JSON, write to disk, and return the JSON string.

        Args:
            report: The ParityReport produced by ComparisonAgent.
            records: Optional list of FeatureRecords to embed in the output.
                     If omitted, all records from the FeatureStore are used.
            filename: Override the output file name.  Defaults to
                      ``parity_output_<timestamp>.json``.

        Returns:
            The JSON string written to disk.
        """
        logger.info("JsonOutputAgent: building JSON output...")

        if records is None:
            records = self._store.get_all()

        payload = {
            "generated_at": report.generated_at.isoformat(),
            "total_features": report.total_features,
            "summary": report.summary,
            "comparisons": {
                key: comp.model_dump(mode="json")
                for key, comp in report.comparisons.items()
            },
            "features": [r.model_dump(mode="json") for r in records],
        }

        json_str = json.dumps(payload, indent=2, default=str)

        if filename is None:
            ts = report.generated_at.strftime("%Y%m%d_%H%M%S")
            filename = f"parity_output_{ts}.json"

        out_path = self._reports_dir / filename
        out_path.write_text(json_str, encoding="utf-8")
        logger.success(f"JsonOutputAgent: JSON output written to {out_path}")

        return json_str
