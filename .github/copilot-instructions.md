# Azure Cloud Feature Parity Bot - Copilot Instructions

## Project Overview
This is a multi-agent AI system that tracks and compares Azure service feature availability across different Azure cloud environments (Commercial, GCC, GCC-High, DoD IL2/IL4/IL5, Azure China, etc.).

## Architecture
- **Multi-agent system** using Python with the **Microsoft Agent Framework** (`agent-framework-azure-ai`, `agent-framework-core`)
- **Foundry-native agents** created via `AzureAIClient` from `agent_framework.azure` — each agent is registered in the Azure AI Foundry project and visible in the ai.azure.com portal
- **Pipeline**: `WorkflowBuilder.add_chain` wires 5 executors: Starter → Research → Extractor → Comparison → Report
- **No Docker** — agents are `PromptAgentDefinition` in Foundry (no container timeout issues)
- **Feature Store**: Persists structured feature parity data as JSON in `data/features/`
- **Deployment**: `infra/deploy_agents.py` registers agents as `PromptAgentDefinition` via `AIProjectClient.agents.create_version()`

## Microsoft Foundry (New)
- Resource type: **`Microsoft Foundry`** (unified, under `Microsoft.CognitiveServices`)
- **No Hub, no separate Storage Account or Key Vault required** — much simpler to provision
- Projects are child resources of the single Foundry resource
- Project endpoint format: `https://<resource-name>.services.ai.azure.com/api/projects/<project-name>`
- **Agents are GA** (not preview)
- Full Foundry SDK & API support, Azure OpenAI-compatible APIs included
- Set `FOUNDRY_PROJECT_ENDPOINT` to the project endpoint from the portal's Home page
- Set `FOUNDRY_MODEL_DEPLOYMENT_NAME` to the model deployment name (e.g. `gpt-4o`)

## Pipeline Executors (agents/executors.py)
- `StarterExecutor` — Parses user query, seeds pipeline state (no LLM)
- `ResearchExecutor` — Fetches Azure parity docs via HTTP; Foundry ChatAgent summarises coverage
- `ExtractorExecutor` — Foundry ChatAgent extracts structured FeatureRecords from docs (or LLM knowledge)
- `ComparisonExecutor` — Pure Python cross-cloud comparison (no LLM)
- `ReportExecutor` — Foundry ChatAgent generates executive summary; Python builds Markdown tables

## Foundry Agents (registered in ai.azure.com)
- `ParityResearchAgent` — Research & summarisation
- `FeatureExtractorAgent` — Structured data extraction
- `ParityReportAgent` — Executive report generation

## Cloud Environments Tracked
- `commercial` - Azure Public (global)
- `gcc` - Azure Government (GCC)
- `gcc_high` - Azure Government High (GCC-High)
- `dod_il2` - DoD IL2
- `dod_il4` - DoD IL4
- `dod_il5` - DoD IL5
- `china` - Azure China (21Vianet)
- `germany` - Azure Germany (legacy)

## Feature Status Values
- `ga` - Generally Available
- `preview` - Public/Private Preview
- `not_available` - Not Available
- `unknown` - Status not determined

## Key Source URLs
- https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-services
- https://learn.microsoft.com/en-us/azure/azure-government/compare-azure-government-global-azure
- https://learn.microsoft.com/en-us/azure/china/
- https://azure.microsoft.com/en-us/updates/

## Conventions
- Use `pydantic` models for all data structures
- Store features as JSON in `data/features/`
- Reports go in `reports/`
- Environment variables managed via `.env` + `python-dotenv`
- All agents are async
- Use `loguru` for logging
