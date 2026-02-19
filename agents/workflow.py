"""Parity analysis workflow definition."""

from __future__ import annotations

from agent_framework import WorkflowBuilder, WorkflowAgent, Workflow

from agents.executors import (
    ComparisonExecutor,
    FeatureExtractorExecutor,
    LearnScraperExecutor,
    ParityStarterExecutor,
    ReportExecutor,
    WebScraperExecutor,
)
from agents.workflow_state import ParityWorkflowState
from storage.feature_store import FeatureStore


def build_parity_workflow() -> Workflow:
    """
    Factory that returns a fresh Workflow each call.
    Used by from_agent_framework() as a per-request factory:
        AgentFrameworkWorkflowAdapter._build_agent() calls factory().as_agent()
    """
    store = FeatureStore()

    starter = ParityStarterExecutor()
    learn_scraper = LearnScraperExecutor()
    web_scraper = WebScraperExecutor()
    extractor = FeatureExtractorExecutor(store=store)
    comparison = ComparisonExecutor()
    reporter = ReportExecutor(store=store)

    workflow: Workflow = (
        WorkflowBuilder()
        .add_chain([starter, learn_scraper, web_scraper, extractor, comparison, reporter])
        .set_start_executor(starter)
        .build()
    )
    return workflow


def build_parity_agent() -> WorkflowAgent:
    """Convenience wrapper for CLI mode — returns a WorkflowAgent."""
    return build_parity_workflow().as_agent()
