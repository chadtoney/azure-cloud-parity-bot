# Azure Cloud Feature Parity Bot - Copilot Instructions

## Project Overview
This is a multi-agent AI system that tracks and compares Azure service feature availability across different Azure cloud environments (Commercial, GCC, GCC-High, DoD IL2/IL4/IL5, Azure China, etc.).

## Architecture
- **Multi-agent system** using Python with `semantic-kernel` or `openai` Agents SDK
- **MCP Integration**: Uses Microsoft Learn MCP and web fetcher tools to gather feature data
- **Feature Store**: Persists structured feature parity data (JSON/SQLite)
- **Orchestrator**: Coordinates scraping, extraction, comparison, and reporting agents

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
