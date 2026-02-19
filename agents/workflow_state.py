"""Shared workflow context state passed between all executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from models.feature import FeatureRecord, ParityReport


@dataclass
class ParityWorkflowState:
    """Mutable state carried through every executor in the parity pipeline."""

    # Set by the starter executor from the incoming chat message
    user_query: str = ""
    target_service: Optional[str] = None   # None = full scan, else targeted run
    extra_urls: List[str] = field(default_factory=list)

    # Populated by scraper executors
    scraped_pages: Dict[str, str] = field(default_factory=dict)

    # Populated by extractor executor
    feature_records: List[FeatureRecord] = field(default_factory=list)

    # Populated by comparison executor
    report: Optional[ParityReport] = None

    # Final human-readable markdown report
    markdown_report: str = ""
