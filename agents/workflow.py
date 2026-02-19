"""
Parity analysis workflow definition.

Builds and returns the Microsoft Agent Framework agent that runs the
full Azure Cloud Feature Parity pipeline as a linear executor chain.
"""

from __future__ import annotations

from agent_framework import WorkflowBuilder, WorkflowAgent

from agents.executors import (
    ComparisonExecutor,
    FeatureExtractorExecutor,
    FutureFeaturesExecutor,
    LearnScraperExecutor,
    ParityStarterExecutor,
    ReportExecutor,
    WebScraperExecutor,
)
from agents.workflow_state import ParityWorkflowState
from storage.feature_store import FeatureStore


def build_parity_agent() -> WorkflowAgent:
    """
    Construct the parity analysis agent from the workflow pipeline.

    Pipeline:
        ParityStarterExecutor  (receives user ChatMessage)
            → LearnScraperExecutor
            → WebScraperExecutor
            → FeatureExtractorExecutor
            → ComparisonExecutor
            → FutureFeaturesExecutor
            → ReportExecutor        (emits AgentRunUpdateEvent)
    """
    store = FeatureStore()

    starter = ParityStarterExecutor()
    learn_scraper = LearnScraperExecutor()
    web_scraper = WebScraperExecutor()
    extractor = FeatureExtractorExecutor(store=store)
    comparison = ComparisonExecutor()
    future_features = FutureFeaturesExecutor()
    reporter = ReportExecutor(store=store)

    agent: WorkflowAgent = (
        WorkflowBuilder()
        .add_chain([starter, learn_scraper, web_scraper, extractor, comparison, future_features, reporter])
        .set_start_executor(starter)
        .build()
        .as_agent()
    )
    return agent
