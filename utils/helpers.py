"""Utility helper functions."""

from __future__ import annotations

import re
from typing import Iterator, List, TypeVar

from models.feature import FeatureStatus

T = TypeVar("T")


def normalize_feature_name(name: str) -> str:
    """Convert a raw feature name to a URL-safe slug ID."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s\-]", "", name)
    name = re.sub(r"[\s\-]+", "-", name)
    return name.strip("-")


def parse_status_string(raw: str) -> FeatureStatus:
    """Map raw text from documentation to a FeatureStatus enum value."""
    normalized = raw.strip().lower()

    ga_patterns = {"ga", "generally available", "available", "yes", "✓", "✔", "supported"}
    preview_patterns = {"preview", "public preview", "private preview", "in preview", "beta"}
    unavailable_patterns = {
        "no",
        "not available",
        "unavailable",
        "not supported",
        "n/a",
        "–",
        "-",
        "✗",
        "✘",
    }

    if normalized in ga_patterns or any(p in normalized for p in ga_patterns):
        return FeatureStatus.GA
    if any(p in normalized for p in preview_patterns):
        return FeatureStatus.PREVIEW
    if normalized in unavailable_patterns or any(p in normalized for p in unavailable_patterns):
        return FeatureStatus.NOT_AVAILABLE

    return FeatureStatus.UNKNOWN


def chunk_list(lst: List[T], size: int) -> Iterator[List[T]]:
    """Yield successive chunks of a given size from a list."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def build_feature_id(service: str, feature: str) -> str:
    """Build a deterministic feature ID from service and feature names."""
    return f"{normalize_feature_name(service)}/{normalize_feature_name(feature)}"
