"""
Azure Cloud Feature Parity Bot – main entry point.

Usage
-----
# HTTP server mode (default – used by Agent Inspector & deployment)
python main.py

# CLI mode (quick ad-hoc run without the HTTP server)
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
from agents.workflow import build_parity_agent  # noqa: E402  (build_parity_workflow used in CLI only)


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
        logger.add(settings.log_file, level=settings.log_level, rotation="10 MB", retention="7 days")


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


async def _warm_credentials() -> None:
    """Acquire an Azure token before accepting traffic to avoid first-request latency.

    DefaultAzureCredential probes multiple credential types in a container
    (EnvironmentCredential, WorkloadIdentity, ManagedIdentity, etc.).  The
    first probe can take 1-5 s.  Doing it before the server starts serving
    keeps that cost off the 30s Foundry request deadline.
    """
    from config.settings import settings
    if not settings.azure_openai_endpoint:
        return
    try:
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        tok = cred.get_token("https://cognitiveservices.azure.com/.default")
        logger.info(f"Credential warm-up OK (token expires {tok.expires_on}).")
    except Exception as exc:  # non-fatal — will retry on first request
        logger.warning(f"Credential warm-up failed (will retry on first request): {exc}")


async def _run_server() -> None:
    """Start the HTTP server backed by the parity agent.

    MS Learn recommended pattern:
        agent = WorkflowBuilder()...build().as_agent()  # WorkflowAgent
        await from_agent_framework(agent).run_async()

    Passing a WorkflowAgent (not a lambda wrapping a bare Workflow) lets the
    hosting adapter correctly register /liveness and /readiness routes that
    Foundry polls before routing traffic.
    """
    from azure.ai.agentserver.agentframework import from_agent_framework
    from agents.feature_extractor import warm_feature_extractor_credential

    # Build the WorkflowAgent ONCE at startup — DefaultAzureCredential init
    # inside FeatureExtractorAgent / ReportGeneratorAgent takes ~600ms each.
    # build_parity_agent() calls build_parity_workflow().as_agent(), which is
    # exactly what MS Learn recommends passing to from_agent_framework().
    logger.info("Building parity agent (one-time startup init)...")
    _agent = build_parity_agent()
    # Warm the credential singleton shared by FeatureExtractorAgent and
    # ReportGeneratorAgent so the managed-identity token is cached before the
    # first request, keeping startup cost off the 30s Foundry deadline.
    logger.info("Warming Azure credentials (shared with all agents)...")
    await warm_feature_extractor_credential()
    logger.info("Starting HTTP server...")
    await from_agent_framework(_agent).run_async()


def main() -> None:
    print("=== Azure Cloud Parity Bot starting ===", flush=True)
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
