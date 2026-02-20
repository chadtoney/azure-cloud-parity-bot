"""
run_workflow.py – Invoke the Foundry UI Workflow via the Responses API.

This script calls the Sequential Workflow you created in the Foundry portal
(Build > Workflows) using the same Responses API pattern as any Foundry agent.

The workflow runs entirely server-side — each agent node (Research → Extractor
→ Report) executes in Foundry with its attached tools (Web Search, Code
Interpreter).

Prerequisites
-------------
1. Deploy agents with tools:         python infra/deploy_agents.py
2. Create a Sequential Workflow in the Foundry portal:
   - Go to ai.azure.com → Build → Workflows → Create new → Sequential
   - Add agents in order: ParityResearchAgent → FeatureExtractorAgent → ParityReportAgent
   - Save the workflow (note the workflow name)
3. Set WORKFLOW_NAME in .env (or pass --workflow-name)

Usage
-----
    python run_workflow.py --query "Check Azure Kubernetes Service parity"
    python run_workflow.py --workflow-name "cloud-parity-pipeline" --query "AKS features"
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(override=True)


def run_workflow(
    query: str,
    workflow_name: str | None = None,
    workflow_version: str | None = None,
    save_report: bool = True,
) -> str:
    """Invoke the Foundry sequential workflow and return the final output.

    Parameters
    ----------
    query : str
        The user query (e.g. "Check AKS government parity").
    workflow_name : str | None
        Name of the workflow in Foundry.  Falls back to WORKFLOW_NAME env var.
    workflow_version : str | None
        Specific version to pin.  Falls back to WORKFLOW_VERSION env var.
        If not set, Foundry uses the latest version.
    save_report : bool
        Whether to save the final report as a local Markdown file.

    Returns
    -------
    str
        The full text output from the workflow.
    """
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import ResponseStreamEventType
    from azure.identity import DefaultAzureCredential

    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        print("ERROR: FOUNDRY_PROJECT_ENDPOINT not set.")
        sys.exit(1)

    wf_name = workflow_name or os.getenv("WORKFLOW_NAME", "cloud-parity-pipeline")
    wf_version = workflow_version or os.getenv("WORKFLOW_VERSION")

    print(f"Connecting to Foundry: {endpoint}")
    print(f"Workflow: {wf_name}" + (f" (v{wf_version})" if wf_version else " (latest)"))
    print(f"Query: {query}\n")

    project_client = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )

    with project_client:
        openai_client = project_client.get_openai_client()

        # ── Create a conversation ────────────────────────────────────
        conversation = openai_client.conversations.create()
        print(f"Conversation created (id: {conversation.id})")

        # ── Build the agent reference ────────────────────────────────
        agent_ref = {"name": wf_name, "type": "agent_reference"}
        if wf_version:
            agent_ref["version"] = wf_version

        # ── Stream the workflow execution ────────────────────────────
        print("\n" + "=" * 60)
        print("WORKFLOW EXECUTION")
        print("=" * 60 + "\n")

        stream = openai_client.responses.create(
            conversation=conversation.id,
            extra_body={"agent": agent_ref},
            input=query,
            stream=True,
            metadata={"x-ms-debug-mode-enabled": "1"},
        )

        full_output = []
        current_action = None

        for event in stream:
            if event.type == ResponseStreamEventType.RESPONSE_OUTPUT_TEXT_DONE:
                full_output.append(event.text)
                print(event.text)

            elif (
                event.type == ResponseStreamEventType.RESPONSE_OUTPUT_ITEM_ADDED
                and hasattr(event.item, "type")
                and event.item.type == "workflow_action"
            ):
                action_id = getattr(event.item, "action_id", "unknown")
                if action_id != current_action:
                    current_action = action_id
                    print(f"\n{'─' * 40}")
                    print(f"  Agent: {action_id}")
                    print(f"{'─' * 40}\n")

            elif (
                event.type == ResponseStreamEventType.RESPONSE_OUTPUT_ITEM_DONE
                and hasattr(event.item, "type")
                and event.item.type == "workflow_action"
            ):
                action_id = getattr(event.item, "action_id", "unknown")
                status = getattr(event.item, "status", "done")
                print(f"  [{action_id}] → {status}")

            elif event.type == ResponseStreamEventType.RESPONSE_OUTPUT_TEXT_DELTA:
                # Real-time streaming delta
                print(event.delta, end="", flush=True)

        print("\n\n" + "=" * 60)
        print("WORKFLOW COMPLETE")
        print("=" * 60)

        # ── Save report locally ──────────────────────────────────────
        final_text = "\n\n".join(full_output) if full_output else "(no output captured)"

        if save_report and full_output:
            reports_dir = pathlib.Path(os.getenv("REPORTS_DIR", "reports"))
            reports_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            report_path = reports_dir / f"parity_report_{ts}.md"
            report_path.write_text(final_text, encoding="utf-8")
            print(f"\nReport saved to {report_path}")

        # ── Clean up conversation ────────────────────────────────────
        try:
            openai_client.conversations.delete(conversation_id=conversation.id)
            print("Conversation deleted.")
        except Exception:
            pass

        return final_text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke the Azure Cloud Parity Bot workflow in Foundry"
    )
    parser.add_argument(
        "--query",
        type=str,
        default="Run full Azure cloud feature parity analysis across all sovereign clouds",
        help="Query to send to the workflow.",
    )
    parser.add_argument(
        "--workflow-name",
        type=str,
        default=None,
        help="Foundry workflow name (default: WORKFLOW_NAME env var or 'cloud-parity-pipeline').",
    )
    parser.add_argument(
        "--workflow-version",
        type=str,
        default=None,
        help="Pin a specific workflow version.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save the report to a local file.",
    )
    args = parser.parse_args()

    run_workflow(
        query=args.query,
        workflow_name=args.workflow_name,
        workflow_version=args.workflow_version,
        save_report=not args.no_save,
    )


if __name__ == "__main__":
    main()
