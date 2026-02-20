"""
deploy_agents.py – Registers pipeline agents as PromptAgentDefinition in
Azure AI Foundry.

Creates each pipeline step as a Foundry-native prompt agent visible in the
ai.azure.com portal.  These agents are the same ones used at runtime by the
Agent Framework workflow.

No Docker container required — agents run natively in Foundry.

Usage
-----
    python infra/deploy_agents.py
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
MODEL = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o")


AGENTS = [
    {
        "name": "ParityResearchAgent",
        "instructions": (
            "You are a research specialist that gathers and summarises Azure "
            "cloud parity documentation from Microsoft Learn and other Azure "
            "sources.  Provide concise summaries of coverage and gaps."
        ),
        "description": (
            "Gathers Azure cloud parity documentation from official sources "
            "and summarises coverage."
        ),
    },
    {
        "name": "FeatureExtractorAgent",
        "instructions": (
            "You are an expert at extracting structured feature availability "
            "data from Azure cloud documentation.  Extract JSON arrays of "
            "feature records with service_name, feature_name, category, "
            "description, status (per cloud: commercial, gcc, gcc_high, "
            "dod_il2, dod_il4, dod_il5, china, germany), source_url, and "
            "notes.  Return ONLY valid JSON."
        ),
        "description": (
            "Extracts structured feature parity records from Azure "
            "documentation using LLM analysis."
        ),
    },
    {
        "name": "ParityReportAgent",
        "instructions": (
            "You are an Azure cloud solutions architect writing executive "
            "summaries.  Given parity statistics, write a clear 3-5 paragraph "
            "summary highlighting gaps, preview features, and actionable "
            "recommendations for sovereign cloud customers."
        ),
        "description": (
            "Generates executive parity report summaries from comparison data."
        ),
    },
]


def deploy() -> None:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    from azure.identity import DefaultAzureCredential

    if not ENDPOINT:
        print(
            "ERROR: FOUNDRY_PROJECT_ENDPOINT not set.\n"
            "Set it in .env or as environment variable.\n"
            "Format: https://<resource>.services.ai.azure.com/api/projects/<project>"
        )
        sys.exit(1)

    print(f"Connecting to Foundry project: {ENDPOINT}")
    client = AIProjectClient(
        endpoint=ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    for agent_def in AGENTS:
        print(f"\nRegistering agent: {agent_def['name']}")
        try:
            agent = client.agents.create_version(
                agent_name=agent_def["name"],
                description=agent_def["description"],
                definition=PromptAgentDefinition(
                    model=MODEL,
                    instructions=agent_def["instructions"],
                ),
            )
            print(
                f"  Created: {agent.name} "
                f"(version={agent.version}, id={agent.id})"
            )
        except Exception as exc:
            print(f"  Failed: {exc}")

    print("\nAll agents registered.  View them at https://ai.azure.com")
    print(
        "\nTo run the pipeline locally:\n"
        "  python main.py              # HTTP server (Agent Inspector)\n"
        "  python main.py --cli        # One-shot CLI mode"
    )


if __name__ == "__main__":
    deploy()
