"""Application configuration using pydantic-settings."""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All configuration loaded from environment / .env file."""

    # ── Azure AI Foundry ──────────────────────────────────────────────────────
    # Project endpoint format:
    #   https://<AIFoundryResourceName>.services.ai.azure.com/api/projects/<ProjectName>
    # Get it from: ai.azure.com → Your Project → Libraries → Foundry
    foundry_project_endpoint: str = Field(
        default="",
        description="Azure AI Foundry project endpoint (required)",
    )
    model_deployment_name: str = Field(
        default="gpt-4o",
        description="Model deployment name in the Foundry project",
    )

    # ── Scraping ──────────────────────────────────────────────────────────────
    scrape_timeout_seconds: int = Field(default=30)
    scrape_max_retries: int = Field(default=3)
    scrape_delay_seconds: float = Field(default=1.0)
    # Set SKIP_SCRAPING=true when outbound internet is restricted (e.g.
    # Foundry hosted agents).  The pipeline will use LLM training knowledge
    # instead of live web fetches.
    skip_scraping: bool = Field(
        default=False,
        description="Skip live web scraping and use LLM knowledge base instead",
    )

    # ── Source URLs ───────────────────────────────────────────────────────────
    source_urls: List[str] = Field(
        default=[
            "https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services",
            "https://learn.microsoft.com/en-us/azure/azure-government/compare-azure-government-global-azure",
            "https://learn.microsoft.com/en-us/azure/china/",
            "https://azure.microsoft.com/en-us/updates/",
        ]
    )

    # ── Storage ───────────────────────────────────────────────────────────────
    data_dir: str = Field(default="data/features")
    reports_dir: str = Field(default="reports")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_file: Optional[str] = Field(default="logs/parity-bot.log")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
