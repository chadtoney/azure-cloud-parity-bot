"""
deploy_agents.py – Registers pipeline agents with tools in Azure AI Foundry.

Creates each pipeline step as a Foundry-native prompt agent with attached
tools (Web Search, Code Interpreter) visible in the ai.azure.com portal.
These agents are designed to be wired into a **UI-based Sequential Workflow**
(Build > Workflows > Sequential) in the Foundry portal.

The workflow runs entirely server-side — no local Python orchestration needed.
Invoke it via the Responses API (see run_workflow.py).

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

# ═══════════════════════════════════════════════════════════════════════════════
#  Agent instructions – self-contained for server-side execution
# ═══════════════════════════════════════════════════════════════════════════════

RESEARCH_INSTRUCTIONS = """\
You are a research specialist that gathers and summarises Azure cloud \
feature parity documentation.

Your job:
1. Use the Web Search tool to search for Azure cloud feature availability \
across different cloud environments (Commercial, GCC, GCC-High, DoD IL2/IL4/IL5, \
Azure China).
2. Focus searches on these key Microsoft Learn pages:
   - https://learn.microsoft.com/azure/azure-government/documentation-government-services
   - https://learn.microsoft.com/azure/azure-government/compare-azure-government-global-azure
   - https://learn.microsoft.com/azure/china/
3. Search for the specific Azure service if one is mentioned in the user query.
4. Summarise what you found: which services are covered, which clouds have \
documented parity data, and any notable gaps or limitations.
5. Include the full text content from the documentation pages you found — the \
next agent in the pipeline needs this raw data to extract features.

Always cite the URLs you retrieved information from.\
"""

EXTRACTION_INSTRUCTIONS = """\
You are an expert at extracting structured feature availability data from \
Azure cloud documentation.

Given documentation text from the Research agent, extract a JSON array of \
feature records.  Use the Code Interpreter tool to validate and format the JSON.

Each record MUST have these fields:
  - service_name: Azure service name (string)
  - feature_name: Specific feature or capability (string)
  - category: Service category (Compute, Networking, Storage, AI, Security, \
Database, Analytics, DevOps, Identity, Management, IoT, Mixed Reality, Other)
  - description: Brief description (string or null)
  - status: object mapping cloud keys to status values
    Keys: commercial, gcc, gcc_high, dod_il2, dod_il4, dod_il5, china, germany
    Values: "ga" (Generally Available), "preview" (Public/Private Preview), \
"not_available" (Not Available), "unknown" (Status not determined)
  - source_url: the documentation URL where this was found (string)
  - notes: any caveats or limitations (string or null)

Rules:
- Extract at least 10 feature records per documentation page
- If documentation text is insufficient, use your training knowledge to fill \
gaps but mark those records with notes: "from LLM knowledge"
- Use Code Interpreter to validate the JSON array is well-formed
- Return ONLY valid JSON — no markdown fences, no explanation text\
"""

REPORT_INSTRUCTIONS = """\
You are an Azure cloud solutions architect that analyses feature parity data \
and writes executive reports.

Given structured JSON feature records from the Extractor agent, you must:

1. Use the Code Interpreter tool to perform cross-cloud comparison analysis:
   - For each sovereign cloud (gcc, gcc_high, dod_il2, dod_il4, dod_il5, \
china), calculate:
     * Total features with data
     * Features that are "ga" in commercial but NOT "ga" in the target cloud
     * Parity percentage = (features_ga_in_target / features_ga_in_commercial) * 100
   - Identify the top 10 most impactful gaps (GA in commercial, not_available \
in sovereign clouds)

2. Generate a Markdown parity report with these sections:
   ## Executive Summary
   3-5 paragraphs highlighting the most significant gaps, features in \
preview that may become GA soon, and actionable recommendations.

   ## Parity Overview Table
   | Cloud | Total | GA | Preview | Not Available | Parity % |
   For each sovereign cloud.

   ## Top Parity Gaps
   Table of the most impactful gaps: service, feature, which clouds are affected.

   ## Recommendations
   Prioritised recommendations for sovereign cloud customers.

   ## Data Sources
   List of URLs the data came from.

Important: Use Code Interpreter for ALL calculations. Do not estimate or \
approximate percentages — compute them from the data.\
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent definitions (with tools)
# ═══════════════════════════════════════════════════════════════════════════════

AGENTS = [
    {
        "name": "ParityResearchAgent",
        "description": (
            "Searches Azure documentation for cloud feature parity data "
            "across Commercial, GCC, GCC-High, DoD, and China environments."
        ),
        "instructions": RESEARCH_INSTRUCTIONS,
        "tools": ["bing_grounding", "code_interpreter"],
    },
    {
        "name": "FeatureExtractorAgent",
        "description": (
            "Extracts structured feature parity JSON records from Azure "
            "documentation using LLM analysis and code validation."
        ),
        "instructions": EXTRACTION_INSTRUCTIONS,
        "tools": ["code_interpreter"],
    },
    {
        "name": "ParityReportAgent",
        "description": (
            "Computes parity statistics and generates executive Markdown "
            "reports from extracted feature data."
        ),
        "instructions": REPORT_INSTRUCTIONS,
        "tools": ["code_interpreter"],
    },
]


def _make_tools(tool_names: list[str]):
    """Convert short tool names to SDK ToolDefinition objects."""
    from azure.ai.agents.models import (
        BingGroundingToolDefinition,
        CodeInterpreterToolDefinition,
    )

    mapping = {
        "code_interpreter": CodeInterpreterToolDefinition,
        "bing_grounding": BingGroundingToolDefinition,
    }
    result = []
    for name in tool_names:
        cls = mapping.get(name)
        if cls:
            if name == "bing_grounding":
                # Bing grounding needs a connection_id; skip if not configured
                conn = os.getenv("BING_CONNECTION_ID", "")
                if conn:
                    from azure.ai.agents.models import BingGroundingSearchConfiguration, BingGroundingSearchToolParameters
                    result.append(BingGroundingToolDefinition(
                        bing_grounding=BingGroundingSearchToolParameters(
                            search_configurations=[
                                BingGroundingSearchConfiguration(connection_id=conn)
                            ]
                        )
                    ))
                else:
                    print(f"  WARN: Skipping bing_grounding (set BING_CONNECTION_ID in .env)")
            else:
                result.append(cls())
        else:
            print(f"  WARN: Unknown tool '{name}', skipping.")
    return result


def deploy() -> None:
    from azure.ai.projects import AIProjectClient
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
        name = agent_def["name"]
        print(f"\nRegistering agent: {name}")
        tools = _make_tools(agent_def["tools"])
        tool_names = [type(t).__name__ for t in tools]
        print(f"  Tools: {tool_names or '(none)'}")

        try:
            # Delete existing agent with same name (idempotent redeploy)
            try:
                existing = client.agents.get_agent(agent_id=name)
                if existing:
                    client.agents.delete_agent(agent_id=existing.id)
                    print(f"  Deleted previous agent: {existing.id}")
            except Exception:
                pass

            agent = client.agents.create_agent(
                model=MODEL,
                name=name,
                description=agent_def["description"],
                instructions=agent_def["instructions"],
                tools=tools if tools else None,
            )
            print(f"  Created: {agent.name} (id={agent.id})")
        except Exception as exc:
            print(f"  Failed: {exc}")

    # List all agents
    print("\n─── All agents in project ───")
    for a in client.agents.list_agents():
        tools_str = ", ".join(t.type for t in (a.tools or []))
        print(f"  {a.name or a.id}: tools=[{tools_str}]")

    print("\nDone.  View agents at https://ai.azure.com")
    print(
        "\nNext steps:\n"
        "  1. Go to ai.azure.com → Build → Workflows → Create new → Sequential\n"
        "  2. Add the 3 agents in order:\n"
        "     ParityResearchAgent → FeatureExtractorAgent → ParityReportAgent\n"
        "  3. Save the workflow\n"
        "  4. Run: python run_workflow.py --query 'Check AKS parity'\n"
    )


if __name__ == "__main__":
    deploy()
