"""Foundry-backed parity analysis workflow.

Creates Foundry ChatAgents via ``AzureAIClient`` and wires them into a
sequential pipeline using ``WorkflowBuilder``.

Agents registered here are visible in the Azure AI Foundry portal at
ai.azure.com under the configured project.
"""

from __future__ import annotations

from typing import Optional

from agent_framework import Workflow, WorkflowAgent, WorkflowBuilder
from agent_framework.azure import AzureAIClient
from azure.identity.aio import DefaultAzureCredential
from loguru import logger

from agents.executors import (
    EXTRACTION_INSTRUCTIONS,
    REPORT_INSTRUCTIONS,
    RESEARCH_INSTRUCTIONS,
    ComparisonExecutor,
    ExtractorExecutor,
    ReportExecutor,
    ResearchExecutor,
    StarterExecutor,
)
from config.settings import settings
from storage.feature_store import FeatureStore


# ═══════════════════════════════════════════════════════════════════════════════
#  Lazy-initialised Foundry client & agents (created once, reused per-request)
# ═══════════════════════════════════════════════════════════════════════════════

_client: Optional[AzureAIClient] = None
_research_agent = None
_extractor_agent = None
_reporter_agent = None


def _ensure_foundry_agents() -> None:
    """Create *AzureAIClient* and the three pipeline ChatAgents on first use.

    Subsequent calls are no-ops — the agents persist for the lifetime of the
    process and are reused across HTTP requests.
    """
    global _client, _research_agent, _extractor_agent, _reporter_agent

    if _client is not None:
        return

    endpoint = settings.foundry_project_endpoint
    model = settings.model_deployment_name

    if not endpoint:
        raise RuntimeError(
            "FOUNDRY_PROJECT_ENDPOINT is required.  Set it in .env or as an "
            "environment variable.\n"
            "Format: https://<resource>.services.ai.azure.com/api/projects/<project>\n"
            "Get it from: ai.azure.com → Your Project → Libraries → Foundry"
        )

    logger.info(f"Initialising Foundry agents (endpoint={endpoint}, model={model})")

    try:
        _client = AzureAIClient(
            project_endpoint=endpoint,
            model_deployment_name=model,
            credential=DefaultAzureCredential(),
        )

        _research_agent = _client.create_agent(
            name="ParityResearchAgent",
            instructions=RESEARCH_INSTRUCTIONS,
        )
        _extractor_agent = _client.create_agent(
            name="FeatureExtractorAgent",
            instructions=EXTRACTION_INSTRUCTIONS,
        )
        _reporter_agent = _client.create_agent(
            name="ParityReportAgent",
            instructions=REPORT_INSTRUCTIONS,
        )

        logger.success("Foundry agents initialised and visible in ai.azure.com.")
    except Exception as exc:
        _client = None          # allow retry on next call
        raise RuntimeError(
            f"Failed to initialise Foundry agents: {exc}\n"
            "Ensure you have run 'az login' and have access to the Foundry project."
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
#  Workflow factory
# ═══════════════════════════════════════════════════════════════════════════════


def build_parity_workflow() -> Workflow:
    """Return a fresh *Workflow* per request.

    Called by ``from_agent_framework()`` as a per-request factory::

        AgentFrameworkWorkflowAdapter._build_agent()  →  factory().as_agent()
    """
    _ensure_foundry_agents()

    store = FeatureStore()

    starter = StarterExecutor()
    research = ResearchExecutor(_research_agent)
    extractor = ExtractorExecutor(_extractor_agent, store=store)
    comparison = ComparisonExecutor(store=store)
    reporter = ReportExecutor(_reporter_agent, store=store)

    workflow: Workflow = (
        WorkflowBuilder()
        .add_chain([starter, research, extractor, comparison, reporter])
        .set_start_executor(starter)
        .build()
    )
    return workflow


def build_parity_agent() -> WorkflowAgent:
    """Convenience wrapper for CLI mode — returns a *WorkflowAgent*."""
    return build_parity_workflow().as_agent()
