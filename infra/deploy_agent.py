"""
deploy_agent.py – Registers and starts the Parity Bot as a Foundry Hosted Agent.

Usage:
    python infra/deploy_agent.py --image <acr-name>.azurecr.io/parity-bot:latest

Run AFTER:
    az acr build --registry <acr-name> --image parity-bot:latest --platform linux/amd64 .
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

PROJECT_ENDPOINT = os.getenv(
    "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
    "https://cloudparitybotproject-resource.services.ai.azure.com/api/projects/cloudparitybotproject",
)
MODEL_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AGENT_NAME = "azure-cloud-parity-bot"


def deploy(image: str) -> None:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import (
        AgentProtocol,
        ImageBasedHostedAgentDefinition,
        ProtocolVersionRecord,
    )
    from azure.identity import DefaultAzureCredential

    print(f"Connecting to Foundry project: {PROJECT_ENDPOINT}")
    client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())

    # Determine next version number
    try:
        existing = client.agents.get(agent_name=AGENT_NAME)
        current_version = int(existing.versions.latest.version)
        next_version = current_version + 1
        print(f"Existing agent found at version {current_version}. Will create version {next_version}.")
        # Stop the running version via the SDK (subprocess shell=True on Windows
        # silently drops list args — the old approach never worked).
        import subprocess
        stop_cmd = (
            f"az cognitiveservices agent stop"
            f" --account-name cloudparitybotproject-resource"
            f" --project-name cloudparitybotproject"
            f" --name {AGENT_NAME}"
            f" --agent-version {current_version}"
        )
        result = subprocess.run(stop_cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Stopped version {current_version}.")
        else:
            print(f"⚠️  Could not stop version {current_version} (may already be stopped): {result.stderr.strip()}")
    except Exception:
        print("No existing agent found. Creating version 1.")

    print(f"Registering agent '{AGENT_NAME}' with image: {image}")
    agent = client.agents.create_version(
        agent_name=AGENT_NAME,
        description="Multi-agent pipeline that tracks and compares Azure service feature availability across Commercial, GCC, GCC-High, DoD, and China clouds.",
        definition=ImageBasedHostedAgentDefinition(
            container_protocol_versions=[
                ProtocolVersionRecord(protocol=AgentProtocol.RESPONSES, version="v1")
            ],
            cpu="1",
            memory="2Gi",
            image=image,
            environment_variables={
                "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": PROJECT_ENDPOINT,
                # NOTE: do NOT set AZURE_AI_PROJECT_ENDPOINT to a real value —
                # the hosting adapter uses that env var to trigger
                # _setup_tracing_with_azure_ai_client(), which calls
                # DefaultAzureCredential().get_token() SYNCHRONOUSLY, blocking
                # the asyncio event loop for 90-100 seconds.
                # We set it to empty string so that even if Foundry injects a
                # value, our explicit definition takes precedence and the empty
                # string is falsy → tracing setup is skipped.
                "AZURE_AI_PROJECT_ENDPOINT": "",
                # Setting APPLICATIONINSIGHTS_CONNECTION_STRING to a non-empty
                # sentinel prevents the agentserver logger from calling
                # AIProjectClient.telemetry.get_application_insights_connection_string()
                # (a blocking HTTPS call that can hang for 60-120 seconds).
                # The string "skip-telemetry" is not a valid connection string,
                # so the Application Insights exporter creation will fail
                # harmlessly and telemetry will be disabled.
                "APPLICATIONINSIGHTS_CONNECTION_STRING": "skip-telemetry",
                # Use services.ai.azure.com endpoint – reachable from Foundry container networking.
                "AZURE_OPENAI_ENDPOINT": "https://cloudparitybotproject-resource.services.ai.azure.com/",
                "AZURE_OPENAI_DEPLOYMENT": MODEL_NAME,
                "AZURE_OPENAI_API_VERSION": os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
                "FAST_AZURE_OPENAI_DEPLOYMENT": "gpt-4o-mini",
                "AGENT_DEBUG_ERRORS": "true",  # expose full errors in responses
                "SKIP_SCRAPING": "true",  # outbound internet blocked in Foundry container
                # Required by the Agent Framework to resolve the project resource.
                "AGENT_PROJECT_RESOURCE_ID": (
                    "/subscriptions/0cc114af-43d6-4d8f-ba1d-cd863a819339"
                    "/resourceGroups/Team2"
                    "/providers/Microsoft.CognitiveServices/accounts"
                    "/cloudparitybotproject-resource"
                ),
            },
        ),
    )
    print(f"✅ Agent registered: {agent.name}  version={agent.version}  id={agent.id}")

    print("Starting agent deployment (min_replicas=1 to prevent cold starts)...")
    # Use REST API directly so we can pass min_replicas=1 — the az CLI does not
    # expose this parameter and defaults to 0 (scale-to-zero), which causes the
    # container to cold-start on every first request and time out Foundry's 30s
    # deadline.
    from azure.identity import DefaultAzureCredential
    import urllib.request as _req
    import json as _json

    base = PROJECT_ENDPOINT.split("/api/projects/")[0]  # https://<resource>.services.ai.azure.com
    project = PROJECT_ENDPOINT.split("/api/projects/")[1]  # cloudparitybotproject
    start_url = (
        f"{base}/api/projects/{project}/agents/{AGENT_NAME}"
        f"/versions/{agent.version}/containers/default:start"
        f"?api-version=2025-11-15-preview"
    )
    cred = DefaultAzureCredential()
    token = cred.get_token("https://ai.azure.com/.default").token
    body = _json.dumps({"min_replicas": 1}).encode()
    http_req = _req.Request(
        start_url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with _req.urlopen(http_req, timeout=30) as resp_raw:
            resp_data = _json.loads(resp_raw.read())
        container = resp_data.get("container", {})
        print(f"✅ Agent deployment started: status={resp_data.get('status')}  "
              f"min_replicas={container.get('min_replicas')}  "
              f"max_replicas={container.get('max_replicas')}")
    except Exception as exc:
        print(f"⚠️  Start request failed: {exc}")
        print(f"Run manually (min_replicas=1 body):")
        print(f"  POST {start_url}")
        print(f'  Body: {{"min_replicas": 1}}')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Full ACR image URL, e.g. paritybotreg.azurecr.io/parity-bot:latest")
    args = parser.parse_args()
    deploy(args.image)
