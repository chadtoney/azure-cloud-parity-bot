# Azure Cloud Feature Parity Bot - Copilot Instructions

## Project Overview
This is a multi-agent AI system that tracks and compares Azure service feature availability across different Azure cloud environments (Commercial, GCC, GCC-High, DoD IL2/IL4/IL5, Azure China, etc.).

## Architecture
- **Multi-agent system** using Python with the **Microsoft Agent Framework** (`agent-framework-azure-ai`, `agent-framework-core`)
- **Pipeline**: `WorkflowBuilder.add_chain` wires executors: Starter → LearnScraper → WebScraper → FeatureExtractor → Comparison → Report
- **Feature Store**: Persists structured feature parity data as JSON in `data/features/`
- **Deployment target**: Microsoft Foundry (new) — see Foundry section below

## Microsoft Foundry: New vs. Classic (Hub-based)

### New Foundry (target for this project)
- Resource type: **`Microsoft Foundry`** (unified, under `Microsoft.CognitiveServices`)
- **No Hub, no separate Storage Account or Key Vault required** — much simpler to provision
- Projects are child resources of the single Foundry resource
- Project endpoint format: `https://<resource-name>.services.ai.azure.com/api/projects/<project-name>`
- **Agents are GA** (not preview)
- Full Foundry SDK & API support, Azure OpenAI-compatible APIs included
- Only the **default project** is visible in the new Foundry portal (`ai.azure.com` with "New Foundry" toggle on)
- Create via Azure Portal → "Azure AI Foundry" resource type, or `az cognitiveservices account create --kind AIServices`
- Set `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` to the project endpoint from the portal's Home page

### Classic / Hub-based (do NOT use for new work)
- Resource type: `Microsoft.MachineLearningServices/workspaces` with `kind: Hub`
- Requires: Hub + Project + Storage Account + Key Vault (4 resources minimum)
- Agents are **preview only**
- Visible in Foundry (classic) portal only
- The `team2parity-hub` resource in Team2 RG is this type — migrate or delete in favor of new Foundry
- The new Foundry portal hides hub-based projects entirely

## Agent Roles
- `orchestrator` - Coordinates all agents, manages workflow
- `learn_scraper` - Fetches docs from Microsoft Learn via MCP
- `web_scraper` - Fetches Azure product pages, sovereign cloud docs, Azure Updates blog
- `feature_extractor` - Parses raw content into structured feature records
- `comparison_agent` - Compares feature status across clouds, detects changes
- `report_generator` - Produces human-readable parity reports

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

## Deployment Prerequisites & Known Gotchas

> These constraints are **mandatory**. Violating any one produces silent failures or
> `ActivationFailed` containers with no diagnostic output. See `LESSONS_LEARNED.md`.

### ACR Image Pull
- ACR **must** be Standard tier with `anonymousPullEnabled=true`
  OR the Foundry resource's **system-assigned** MI must have `AcrPull` on the registry
- The user-assigned MI and the project workload MI are NOT used for image pulls

### RBAC — Project Workload MI
The container runs as the **project workload MI** (`<account>/projects/<project>`).
Grant it both roles on the AI Services resource:
- `Cognitive Services OpenAI User`
- `Azure AI User`

### Code Constraints — CRITICAL
- **Files with `@handler` methods MUST NOT have `from __future__ import annotations`**
  (breaks runtime type validation in agent_framework; all annotations become strings)
- `WorkflowContext` annotations **must** be subscripted: `WorkflowContext[dict]`
- `main.py` **must** emit `sys.stdout.write("ALIVE1...\n"); sys.stdout.flush()` before
  any third-party import — enables diagnosing image pull failures vs Python crashes
- Startup must not block on credential warm-up; wrap with `asyncio.wait_for(..., timeout=10)`

### Container Environment Variables (set in `infra/deploy_agent.py`)
```python
"APPLICATIONINSIGHTS_CONNECTION_STRING": "skip-telemetry",  # prevents blocking telemetry init
"AZURE_AI_PROJECT_ENDPOINT": "",                             # prevents blocking tracing setup
"SKIP_SCRAPING": "true",                                     # if outbound internet blocked
```

### Deployment — `min_replicas: 1`
- Default `min_replicas=0` (scale-to-zero) means ~60–120s cold start — exceeds Foundry's 30s timeout
- Set `min_replicas=1` via the Foundry REST API start endpoint (az CLI doesn't expose this parameter)

### Debugging Deployed Containers
1. Check container state: `GET .../agents/{name}/versions/{ver}/containers/default`
2. Stream logs: `GET .../agents/{name}/versions/{ver}/containers/default:logstream`
3. No ALIVE lines → image pull auth failure (check ACR anon pull / Foundry system MI)
4. ALIVE1–4 but no server → import crash or `from __future__ import annotations` in handler file
5. Server up, liveness 200, but 401 → project workload MI missing RBAC
6. Foundry returns `client_disconnect` at ~101s → container is `ActivationFailed`, not an app timeout
