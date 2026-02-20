"""
Azure Cloud Feature Parity Bot – main entry point.

Two execution modes:

1. **Foundry Workflow** (recommended) – invokes the sequential workflow
   running entirely server-side in Foundry with Web Search + Code Interpreter
   tools.  See ``run_workflow.py`` for the dedicated client.

2. **Local pipeline** (legacy) – uses Agent Framework WorkflowBuilder to
   orchestrate Foundry ChatAgents locally.  Useful for development/debugging.

Usage
-----
# Invoke the Foundry UI Workflow (recommended)
python main.py --query "Check Azure Kubernetes Service government parity"

# Legacy: local pipeline via Agent Framework (development only)
python main.py --local --query "Check AKS parity"

# Legacy: HTTP server mode for Agent Inspector
python main.py --local --server
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
from loguru import logger

# Load env FIRST, with override=True so deployed env vars take precedence
load_dotenv(override=True)

from config.settings import settings  # noqa: E402 – must be after load_dotenv


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


def _run_foundry_workflow(query: str) -> None:
    """Invoke the Foundry portal Sequential Workflow via Responses API."""
    from run_workflow import run_workflow

    logger.info(f"Invoking Foundry workflow with query: {query!r}")
    run_workflow(query=query)


async def _run_local_cli(query: str) -> None:
    """Run the local Agent Framework pipeline once (legacy/dev mode)."""
    from agent_framework import ChatMessage, TextContent, Role
    from agents.workflow import build_parity_agent

    agent = build_parity_agent()
    messages = [
        ChatMessage(
            role=Role.USER,
            contents=[TextContent(text=query)],
        )
    ]
    logger.info(f"Running local CLI pipeline with query: {query!r}")
    response = await agent.run(messages)
    for msg in response.messages:
        if msg.role == Role.ASSISTANT:
            for part in msg.contents or []:
                if hasattr(part, "text"):
                    print(part.text)


async def _run_local_server() -> None:
    """Start the local HTTP server backed by the parity workflow (legacy)."""
    from azure.ai.agentserver.agentframework import from_agent_framework
    from agents.workflow import build_parity_workflow

    logger.info("Starting Azure Cloud Parity Bot local HTTP server...")
    await from_agent_framework(build_parity_workflow).run_async()


def main() -> None:
    import asyncio

    _configure_logging()

    parser = argparse.ArgumentParser(description="Azure Cloud Feature Parity Bot")
    parser.add_argument(
        "--query",
        type=str,
        default="Run full Azure cloud feature parity analysis across all sovereign clouds",
        help="Query to send to the workflow.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local Agent Framework pipeline instead of Foundry workflow.",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="Start local HTTP server (only with --local).",
    )
    args = parser.parse_args()

    if args.local:
        if args.server:
            asyncio.run(_run_local_server())
        else:
            asyncio.run(_run_local_cli(args.query))
    else:
        _run_foundry_workflow(args.query)


if __name__ == "__main__":
    main()
