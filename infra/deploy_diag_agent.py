"""
deploy_diag_agent.py – Deploys the diagnostic server as a Foundry Hosted Agent.

This agent is identical in protocol to the real parity bot but streams a
health/diagnostic report instead of running the pipeline.  Use it to isolate
whether timeouts originate from the Foundry↔container network, credential
acquisition, or the OpenAI call.

Usage:
    # Build the diagnostic image
    az acr build --registry cloudparitybotreg --image parity-bot-diag:d1 \
                 --platform linux/amd64 -f Dockerfile.diag .

    # Deploy as a Foundry agent
    python infra/deploy_diag_agent.py --image cloudparitybotreg.azurecr.io/parity-bot-diag:d1
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

PROJECT_ENDPOINT = os.getenv(
    "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
    "https://cloudparitybotproject-resource.services.ai.azure.com/api/projects/cloudparitybotproject",
)
DIAG_AGENT_NAME = "azure-cloud-parity-diag"


def deploy(image: str) -> None:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import (
        AgentProtocol,
        ImageBasedHostedAgentDefinition,
        ProtocolVersionRecord,
    )
    from azure.identity import DefaultAzureCredential

    print(f"Connecting to project: {PROJECT_ENDPOINT}")
    client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())

    # Determine next version
    try:
        existing = client.agents.get(agent_name=DIAG_AGENT_NAME)
        current_version = int(existing.versions.latest.version)
        next_version = current_version + 1
        print(f"Existing diag agent at v{current_version} → will create v{next_version}.")

        stop_cmd = (
            f"az cognitiveservices agent stop"
            f" --account-name cloudparitybotproject-resource"
            f" --project-name cloudparitybotproject"
            f" --name {DIAG_AGENT_NAME}"
            f" --agent-version {current_version}"
        )
        result = subprocess.run(stop_cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Stopped v{current_version}.")
        else:
            print(f"⚠️  Stop v{current_version}: {result.stderr.strip()}")
    except Exception:
        print("No existing diag agent found. Creating v1.")

    print(f"Registering '{DIAG_AGENT_NAME}' with image: {image}")
    agent = client.agents.create_version(
        agent_name=DIAG_AGENT_NAME,
        description="Diagnostic agent — streams credential/network health checks instead of running the parity pipeline.",
        definition=ImageBasedHostedAgentDefinition(
            container_protocol_versions=[
                ProtocolVersionRecord(protocol=AgentProtocol.RESPONSES, version="v1")
            ],
            cpu="0.5",
            memory="1Gi",
            image=image,
            environment_variables={
                # Forward the same env vars as the real bot so the checks are
                # representative of what the real bot would see.
                "AZURE_OPENAI_ENDPOINT":      "https://cloudparitybotproject-resource.services.ai.azure.com/",
                "AZURE_OPENAI_DEPLOYMENT":    "gpt-4o",
                "AZURE_OPENAI_API_VERSION":   "2024-12-01-preview",
                "FAST_AZURE_OPENAI_DEPLOYMENT": "gpt-4o-mini",
                "AGENT_PROJECT_RESOURCE_ID": (
                    "/subscriptions/0cc114af-43d6-4d8f-ba1d-cd863a819339"
                    "/resourceGroups/Team2"
                    "/providers/Microsoft.CognitiveServices/accounts"
                    "/cloudparitybotproject-resource"
                ),
            },
        ),
    )
    print(f"✅ Registered: {agent.name}  version={agent.version}")

    start_cmd = (
        f"az cognitiveservices agent start"
        f" --account-name cloudparitybotproject-resource"
        f" --project-name cloudparitybotproject"
        f" --name {DIAG_AGENT_NAME}"
        f" --agent-version {agent.version}"
    )
    result = subprocess.run(start_cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print("✅ Diag agent started successfully.")
        print(result.stdout)
    else:
        print(f"⚠️  Start failed: {result.stderr}")
        print(f"Run manually:\n  {start_cmd}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Full ACR image URL")
    args = parser.parse_args()
    deploy(args.image)
