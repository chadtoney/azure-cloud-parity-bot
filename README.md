# Azure Cloud Feature Parity Bot

A **multi-agent AI solution** built on the **Microsoft Agent Framework** that tracks and compares Azure service feature availability across all Azure cloud environments.

## Clouds Tracked

| Key | Environment |
|-----|-------------|
| `commercial` | Azure Public (global) |
| `gcc` | Azure Government (GCC) |
| `gcc_high` | Azure Government High (GCC-High) |
| `dod_il2` | DoD IL2 |
| `dod_il4` | DoD IL4 |
| `dod_il5` | DoD IL5 |
| `china` | Azure China (21Vianet) |
| `germany` | Azure Germany (legacy) |

## Architecture

```
[User Message]
      │
      ▼
ParityStarterExecutor    ← parses intent / service name
      │
      ▼
LearnScraperExecutor     ← fetches Microsoft Learn parity docs (MCP)
      │
      ▼
WebScraperExecutor       ← fetches Azure Updates + sovereign cloud pages
      │
      ▼
FeatureExtractorExecutor ← LLM-powered HTML → FeatureRecord extraction
      │
      ▼
ComparisonExecutor       ← builds cross-cloud parity comparisons
      │
      ▼
ReportExecutor           ← generates Markdown report + HTTP response
```

Built with `WorkflowBuilder.add_chain` – all inter-step data flows through `ctx.set_shared_state / ctx.get_shared_state`.

## Prerequisites

- Python 3.10+
- **Microsoft Foundry (new)** resource with a model deployment (see below)

## Microsoft Foundry: New vs. Classic

This project targets **Microsoft Foundry (new)** — not the older hub-based (classic) architecture.

| | New Foundry (use this) | Classic / Hub-based (legacy) |
|---|---|---|
| Azure resource type | `Microsoft Foundry` (`AIServices` kind) | `Microsoft.MachineLearningServices/workspaces` Hub |
| Dependencies | None — single resource | Hub + Project + Storage + Key Vault |
| Agents | ✅ GA | ⚠️ Preview only |
| Foundry SDK & API | ✅ Full | ⚠️ Limited |
| Portal | [ai.azure.com](https://ai.azure.com) — "New Foundry" toggle ON | [ai.azure.com](https://ai.azure.com) — "New Foundry" toggle OFF (classic) |
| Project endpoint | `https://<resource>.services.ai.azure.com/api/projects/<project>` | `https://<region>.api.azureml.ms/...` |

> **Note:** The `team2parity-hub` resource provisioned in Team2 RG is hub-based (classic). For new deployments, create a **Microsoft Foundry** resource instead.

## Setup

```bash
# 1. Create & activate virtual environment (already done if you cloned fresh)
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
#    Edit .env and fill in your Azure OpenAI values
```

### `.env` values to update

| Variable | Description |
|----------|-------------|
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | New Foundry project endpoint — find it on the project Home page in [ai.azure.com](https://ai.azure.com) (New Foundry toggle ON) |
| `AZURE_OPENAI_ENDPOINT` | Your Azure OpenAI / Foundry resource endpoint URL |
| `AZURE_OPENAI_API_KEY` | API key (or leave blank + use `az login` for Entra ID auth) |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name (e.g. `gpt-4o`) |

> **Tip:** With New Foundry, `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` is the primary connection. Azure OpenAI-compatible APIs are included — no separate Azure OpenAI resource required.

## Running

### HTTP Server mode (recommended – works with Agent Inspector)

```bash
# Start with agentdev instrumentation
.venv\Scripts\python.exe -m agentdev run main.py --verbose --port 8087

# Or press F5 in VS Code to debug with Agent Inspector
```

### CLI mode (quick ad-hoc analysis)

```bash
.venv\Scripts\python.exe main.py --cli
# Targeted service check:
.venv\Scripts\python.exe main.py --cli --query "Check Azure Kubernetes Service government parity"
```

## VS Code Debugging

Use the **Run and Debug** panel and select:

- **Debug Parity Bot (HTTP Server)** – starts the HTTP server + opens Agent Inspector
- **Debug Parity Bot (CLI)** – runs a single pipeline pass in the terminal

## Output

- **JSON feature store**: `data/features/<category>.json`
- **Reports**: `reports/parity_report_<timestamp>.json` + `.md`
- **Logs**: `logs/parity-bot.log`

## Project Structure

```
├── main.py                  # Entry point (HTTP server + CLI)
├── agents/
│   ├── executors.py         # Agent Framework Executor classes (pipeline steps)
│   ├── workflow.py          # WorkflowBuilder wiring
│   ├── workflow_state.py    # Shared state dataclass (reference)
│   ├── comparison_agent.py  # Cross-cloud comparison logic
│   ├── feature_extractor.py # LLM + heuristic HTML extraction
│   ├── learn_scraper.py     # Microsoft Learn scraper
│   ├── orchestrator.py      # Standalone orchestrator (non-framework mode)
│   ├── report_generator.py  # Markdown report + LLM summary
│   └── web_scraper.py       # General web scraper
├── clients/
│   ├── ms_learn_client.py   # Microsoft Learn HTTP client
│   └── web_client.py        # General HTTP client
├── config/settings.py       # Pydantic-settings configuration
├── models/feature.py        # Pydantic data models
├── storage/feature_store.py # JSON file-backed feature store
├── utils/helpers.py         # Normalisation / parsing helpers
├── data/features/           # Persisted feature records (gitignored)
├── reports/                 # Generated parity reports (gitignored)
└── .env                     # Credentials (gitignored)
```
