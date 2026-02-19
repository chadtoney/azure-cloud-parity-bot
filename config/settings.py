"""Application configuration using pydantic-settings."""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All configuration loaded from environment / .env file."""

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    azure_openai_endpoint: str = Field(default="", description="Azure OpenAI endpoint URL")
    azure_openai_api_key: str = Field(default="", description="Azure OpenAI API key")
    azure_openai_deployment: str = Field(default="gpt-4o", description="Chat completion deployment name")
    azure_openai_api_version: str = Field(default="2024-12-01-preview")

    # ── Azure AI Foundry (Agents Service) ────────────────────────────────────
    # Project endpoint format: https://<account>.services.ai.azure.com/api/projects/<project>
    azure_ai_foundry_project_endpoint: str = Field(
        default="",
        description="Azure AI Foundry project endpoint (enables Foundry-native agents visible in portal)",
    )
    # Name of the agent as it will appear in the Foundry portal
    azure_ai_foundry_agent_name: str = Field(
        default="Azure Cloud Feature Parity Bot",
        description="Display name for the agent in Azure AI Foundry portal",
    )

    # ── Agent Settings ────────────────────────────────────────────────────────
    agent_max_iterations: int = Field(default=10)
    agent_temperature: float = Field(default=0.0)

    # ── MCP / Scraping ────────────────────────────────────────────────────────
    ms_learn_mcp_url: str = Field(
        default="https://learn.microsoft.com",
        description="Base URL for Microsoft Learn MCP server",
    )
    scrape_timeout_seconds: int = Field(default=30)
    scrape_max_retries: int = Field(default=3)
    scrape_delay_seconds: float = Field(default=1.0)
    # Set SKIP_SCRAPING=true in environments where outbound internet is
    # restricted (e.g. Foundry hosted-agent containers). The pipeline will
    # use the LLM's training knowledge instead of live web fetches.
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
