"""Persistent feature store backed by JSON files."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from config.settings import settings
from models.feature import CloudEnvironment, FeatureRecord, FeatureStatus, ParityReport


class FeatureStore:
    """
    Simple file-backed store for FeatureRecord objects.

    Data is persisted as JSON files under `data_dir/` – one file per
    Azure service category (e.g. data/features/compute.json).
    A combined index file `data/features/_index.json` maps feature IDs
    to their category files for fast look-up.
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self._data_dir = Path(data_dir or settings.data_dir)
        self._reports_dir = Path(settings.reports_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, FeatureRecord] = {}
        self._load_all()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _feature_file(self, category: str) -> Path:
        safe = category.lower().replace(" ", "_").replace("/", "_")
        return self._data_dir / f"{safe}.json"

    def _load_all(self) -> None:
        """Load all feature JSON files into memory cache."""
        for path in self._data_dir.glob("*.json"):
            if path.name.startswith("_"):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    records = json.load(fh)
                for raw in records:
                    record = FeatureRecord(**raw)
                    self._cache[record.id] = record
            except Exception as exc:
                logger.warning(f"Failed to load {path}: {exc}")
        logger.info(f"Loaded {len(self._cache)} feature records from disk.")

    def _save_category(self, category: str) -> None:
        """Persist all records for a given category to disk."""
        records = [r for r in self._cache.values() if r.category == category]
        path = self._feature_file(category)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([r.model_dump(mode="json") for r in records], fh, indent=2, default=str)

    # ── Public API ────────────────────────────────────────────────────────────

    def upsert(self, record: FeatureRecord) -> None:
        """Insert or update a feature record."""
        record.last_updated = datetime.utcnow()
        self._cache[record.id] = record
        self._save_category(record.category)

    def upsert_many(self, records: List[FeatureRecord]) -> None:
        categories = set()
        for record in records:
            record.last_updated = datetime.utcnow()
            self._cache[record.id] = record
            categories.add(record.category)
        for cat in categories:
            self._save_category(cat)
        logger.info(f"Upserted {len(records)} records across {len(categories)} categories.")

    def get(self, feature_id: str) -> Optional[FeatureRecord]:
        return self._cache.get(feature_id)

    def get_all(self) -> List[FeatureRecord]:
        return list(self._cache.values())

    def get_by_category(self, category: str) -> List[FeatureRecord]:
        return [r for r in self._cache.values() if r.category.lower() == category.lower()]

    def get_parity_gaps(
        self,
        baseline: CloudEnvironment = CloudEnvironment.COMMERCIAL,
    ) -> List[FeatureRecord]:
        """Return features that are GA in baseline but not in some other env."""
        return [r for r in self._cache.values() if r.is_parity_gap(baseline)]

    def save_report(self, report: ParityReport, filename: Optional[str] = None) -> Path:
        """Persist a parity report as JSON."""
        if filename is None:
            ts = report.generated_at.strftime("%Y%m%d_%H%M%S")
            filename = f"parity_report_{ts}.json"
        path = self._reports_dir / filename
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report.model_dump(mode="json"), fh, indent=2, default=str)
        logger.info(f"Report saved to {path}")
        return path

    def load_latest_report(self) -> Optional[ParityReport]:
        """Load the most recently generated parity report."""
        reports = sorted(self._reports_dir.glob("parity_report_*.json"), reverse=True)
        if not reports:
            return None
        with open(reports[0], "r", encoding="utf-8") as fh:
            return ParityReport(**json.load(fh))
