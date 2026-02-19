"""Pydantic models for Azure Cloud Feature Parity data."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class CloudEnvironment(str, Enum):
    """Azure cloud environments tracked by the bot."""

    COMMERCIAL = "commercial"
    GCC = "gcc"
    GCC_HIGH = "gcc_high"
    DOD_IL2 = "dod_il2"
    DOD_IL4 = "dod_il4"
    DOD_IL5 = "dod_il5"
    CHINA = "china"
    GERMANY = "germany"  # Legacy


class FeatureStatus(str, Enum):
    """Feature availability status in a cloud environment."""

    GA = "ga"
    PREVIEW = "preview"
    NOT_AVAILABLE = "not_available"
    UNKNOWN = "unknown"


class FeatureRecord(BaseModel):
    """A single Azure service feature and its availability across clouds."""

    id: str = Field(..., description="Unique identifier: service/feature slug")
    service_name: str = Field(..., description="Azure service name, e.g. 'Azure Kubernetes Service'")
    feature_name: str = Field(..., description="Specific feature or capability name")
    category: str = Field(default="General", description="Service category, e.g. 'Compute', 'Networking'")
    description: Optional[str] = Field(default=None, description="Short description of the feature")

    # Status per cloud environment
    status: Dict[CloudEnvironment, FeatureStatus] = Field(
        default_factory=lambda: {env: FeatureStatus.UNKNOWN for env in CloudEnvironment},
        description="Feature availability status per cloud environment",
    )

    # Metadata
    source_url: Optional[str] = Field(default=None, description="Source documentation URL")
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = Field(default=None, description="Additional notes or caveats")

    class Config:
        use_enum_values = True

    def get_status(self, env: CloudEnvironment) -> FeatureStatus:
        return self.status.get(env, FeatureStatus.UNKNOWN)

    def is_parity_gap(self, baseline: CloudEnvironment = CloudEnvironment.COMMERCIAL) -> bool:
        """Returns True if any non-baseline environment lacks a feature available in baseline."""
        baseline_status = self.get_status(baseline)
        if baseline_status != FeatureStatus.GA:
            return False
        return any(
            self.get_status(env) == FeatureStatus.NOT_AVAILABLE
            for env in CloudEnvironment
            if env != baseline
        )


class FeatureComparison(BaseModel):
    """Comparison result for a set of features between two cloud environments."""

    baseline_cloud: CloudEnvironment
    target_cloud: CloudEnvironment
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    ga_in_both: List[str] = Field(default_factory=list, description="Feature IDs available in both clouds")
    ga_in_baseline_only: List[str] = Field(
        default_factory=list,
        description="Feature IDs GA in baseline but not in target",
    )
    preview_in_target: List[str] = Field(
        default_factory=list,
        description="Feature IDs in preview in target cloud",
    )
    not_available_in_target: List[str] = Field(
        default_factory=list,
        description="Feature IDs not available in target cloud",
    )

    @property
    def parity_percentage(self) -> float:
        """Percentage of baseline GA features also GA in target."""
        total = len(self.ga_in_baseline_only) + len(self.ga_in_both)
        if total == 0:
            return 100.0
        return round(len(self.ga_in_both) / total * 100, 2)


class ParityReport(BaseModel):
    """Full parity report across all tracked cloud environments."""

    generated_at: datetime = Field(default_factory=datetime.utcnow)
    total_features: int = 0
    comparisons: Dict[str, FeatureComparison] = Field(
        default_factory=dict,
        description="Keyed by '{baseline}_{target}'",
    )
    summary: Optional[str] = Field(default=None, description="Human-readable summary from report agent")


class ScrapeJob(BaseModel):
    """A scraping job definition."""

    url: str
    source_type: str = Field(..., description="'ms_learn' | 'azure_updates' | 'sovereign_docs' | 'web'")
    target_clouds: List[CloudEnvironment] = Field(default_factory=list)
    priority: int = Field(default=5, ge=1, le=10)
    metadata: Dict[str, str] = Field(default_factory=dict)
