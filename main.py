"""
Azure Cloud Feature Parity Bot – main entry point.

Usage
-----
# HTTP server mode (default – used by Agent Inspector & deployment)
python main.py

# CLI mode (quick ad-hoc run without the HTTP server)
python main.py --cli --query "Check Azure Kubernetes Service government parity"
"""

# ── CRASH DIAGNOSTICS: raw writes BEFORE any third-party imports ──────────────
# NOTE: `from __future__ import annotations` is intentionally OMITTED – it must
# appear before all other statements (except docstrings/comments) which would
# prevent the early-logging block below.  main.py uses no complex type aliases
# that require it, so the omission is safe.
#
# PYTHONUNBUFFERED=1 in the Dockerfile ensures these writes appear immediately
# in the container logstream.  If ALIVE1 never shows up we know Python is
# killed before it runs a single bytecode instruction (OOM, bad entrypoint…).
import sys
import os

sys.stdout.write("ALIVE1 – Python started\n"); sys.stdout.flush()
sys.stderr.write("ALIVE1 – Python started\n"); sys.stderr.flush()


def _early_log(msg: str) -> None:
    sys.stdout.write(msg + "\n"); sys.stdout.flush()
    sys.stderr.write(msg + "\n"); sys.stderr.flush()


_early_log(f"ALIVE2 – Python {sys.version}")
_early_log(f"ALIVE3 – cwd={os.getcwd()}")
_early_log(f"ALIVE4 – key env: SKIP_SCRAPING={os.getenv('SKIP_SCRAPING')} "
           f"AZURE_OPENAI_ENDPOINT={os.getenv('AZURE_OPENAI_ENDPOINT', '(not set)')[:50]}")

# ── stdlib imports ─────────────────────────────────────────────────────────────
try:
    import argparse
    import asyncio
    _early_log("ALIVE5 – stdlib OK")
except Exception as _e:
    _early_log(f"ERR stdlib: {_e}")
    sys.exit(1)

# ── third-party base ──────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _early_log("ALIVE6 – dotenv OK")
except Exception as _e:
    _early_log(f"ERR dotenv: {_e}")
    sys.exit(1)

try:
    from loguru import logger
    _early_log("ALIVE7 – loguru OK")
except Exception as _e:
    _early_log(f"ERR loguru: {_e}")
    sys.exit(1)

# ── env + settings ────────────────────────────────────────────────────────────
try:
    load_dotenv(override=True)
    _early_log("ALIVE8 – load_dotenv OK")
except Exception as _e:
    _early_log(f"ERR load_dotenv: {_e}")

try:
    from config.settings import settings  # noqa: E402 – must be after load_dotenv
    _early_log(f"ALIVE9 – settings OK (skip_scraping={settings.skip_scraping})")
except Exception as _e:
    _early_log(f"ERR settings (FATAL): {_e}")
    sys.exit(1)

# ── agent workflow import ─────────────────────────────────────────────────────
try:
    from agents.workflow import build_parity_agent
    _early_log("ALIVE10 – build_parity_agent imported OK")
except Exception as _e:
    _early_log(f"ERR agents.workflow (FATAL): {_e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ── END DIAGNOSTICS – normal startup proceeds below ───────────────────────────


def _configure_logging() -> None:
    logger.remove()
    # Use stdout so logs appear in the container log stream (Foundry captures stdout).
    # colorize=False: avoid ANSI escape codes that corrupt non-TTY output.
    logger.add(
        sys.stdout,
        level=settings.log_level,
        colorize=False,
        format="{time:HH:mm:ss} | {level: <8} | {message}",
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
    import time as _time

    def _checkpoint(msg: str) -> None:
        """Write to both streams so we see it regardless of container log capture."""
        ts = f"[STARTUP {_time.time()-_t0:.2f}s]"
        line = f"{ts} {msg}\n"
        sys.stdout.write(line); sys.stdout.flush()
        sys.stderr.write(line); sys.stderr.flush()

    _t0 = _time.time()
    _checkpoint("build_parity_agent starting")
    _agent = build_parity_agent()
    _checkpoint(f"build_parity_agent done in {_time.time()-_t0:.2f}s")
    # Warm the credential singleton shared by FeatureExtractorAgent and
    # ReportGeneratorAgent so the managed-identity token is cached before the
    # first request, keeping startup cost off the 30s Foundry deadline.
    _checkpoint("warming Azure credentials (timeout 10s)")
    _t1 = _time.time()
    try:
        await asyncio.wait_for(warm_feature_extractor_credential(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("warm_feature_extractor_credential timed out after 10s — uvicorn will start anyway, retry on first request")
    _checkpoint(f"credential warm-up done in {_time.time()-_t1:.2f}s")
    _checkpoint(f"total pre-server init: {_time.time()-_t0:.2f}s — starting uvicorn")
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
