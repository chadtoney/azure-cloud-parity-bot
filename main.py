"""
Azure Cloud Feature Parity Bot – main entry point.

Uses Foundry-native agents orchestrated via Agent Framework WorkflowBuilder.
Each pipeline step is backed by a ChatAgent registered in the Azure AI Foundry
project and visible in the ai.azure.com portal.

Usage
-----
# HTTP server mode (default – for Agent Inspector & Foundry deployment)
python main.py

# CLI mode (quick ad-hoc run)
python main.py --cli --query "Check Azure Kubernetes Service government parity"
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv
from loguru import logger

# Load env FIRST, with override=True so deployed env vars take precedence
load_dotenv(override=True)

from config.settings import settings  # noqa: E402 – must be after load_dotenv
from agents.workflow import build_parity_workflow, build_parity_agent  # noqa: E402


def _configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    if settings.log_file:
        import pathlib

        pathlib.Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            settings.log_file,
            level=settings.log_level,
            rotation="10 MB",
            retention="7 days",
        )


async def _run_cli(query: str) -> None:
    """Run the parity pipeline once via CLI and print the Markdown report."""
    from agent_framework import ChatMessage, TextContent, Role

    agent = build_parity_agent()
    messages = [
        ChatMessage(
            role=Role.USER,
            contents=[TextContent(text=query)],
        )
    ]
    logger.info(f"Running CLI pipeline with query: {query!r}")
    response = await agent.run(messages)
    for msg in response.messages:
        if msg.role == Role.ASSISTANT:
            for part in msg.contents or []:
                if hasattr(part, "text"):
                    print(part.text)


async def _run_server() -> None:
    """Start the HTTP server backed by the parity workflow."""
    from azure.ai.agentserver.agentframework import from_agent_framework

    # Pass build_parity_workflow as a factory (not a pre-built agent).
    # AgentFrameworkWorkflowAdapter._build_agent() calls factory().as_agent()
    # to create a fresh WorkflowAgent per conversation request.
    logger.info("Starting Azure Cloud Parity Bot HTTP server...")
    await from_agent_framework(build_parity_workflow).run_async()


def main() -> None:
    _configure_logging()

    parser = argparse.ArgumentParser(description="Azure Cloud Feature Parity Bot")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run once in CLI mode instead of starting the HTTP server.",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="Run full Azure cloud feature parity analysis",
        help="Query to use in CLI mode.",
    )
    args = parser.parse_args()

    if args.cli:
        asyncio.run(_run_cli(args.query))
    else:
        asyncio.run(_run_server())


if __name__ == "__main__":
    main()
