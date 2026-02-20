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
        # Stop the running version before creating a new one
        import subprocess
        subprocess.run(
            ["az", "cognitiveservices", "agent", "stop",
             "--account-name", "cloudparitybotproject-resource",
             "--project-name", "cloudparitybotproject",
             "--name", AGENT_NAME,
             "--agent-version", str(current_version)],
            shell=True, capture_output=True
        )
        print(f"Stopped version {current_version}.")
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
                "AZURE_AI_PROJECT_ENDPOINT": PROJECT_ENDPOINT,  # expected by hosting adapter
                # Use services.ai.azure.com endpoint – reachable from within Foundry container
                # networking (cognitiveservices.azure.com may be blocked).
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

    print("Starting agent deployment...")
    import subprocess

    cmd = [
        "az", "cognitiveservices", "agent", "start",
        "--account-name", "cloudparitybotproject-resource",
        "--project-name", "cloudparitybotproject",
        "--name", AGENT_NAME,
        "--agent-version", str(agent.version),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    if result.returncode == 0:
        print("✅ Agent deployment started successfully.")
        print(result.stdout)
    else:
        print("⚠️  az cognitiveservices agent start failed:")
        print(result.stderr)
        print("You can start it manually with:")
        print("  " + " ".join(cmd))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Full ACR image URL, e.g. paritybotreg.azurecr.io/parity-bot:latest")
    args = parser.parse_args()
    deploy(args.image)
