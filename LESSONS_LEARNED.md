# Lessons Learned — Foundry Hosted Agent Deployment

> **Context:** This document captures hard-won lessons from debugging 18 container deployment
> iterations for a Microsoft Agent Framework agent on Azure AI Foundry Hosted Agents.
> It is written for teams doing **spec-driven vibe coding** — using an AI coding agent to
> generate, iterate on, and deploy production Azure workloads.

---

## 1. The Crash Hierarchy: Always Check Top-Down

Container failures present the same surface symptom regardless of cause: `ActivationFailed`,
liveness probe timeout, or Foundry returning `client_disconnect` at 100s. Debug in this order:

```
1. Can the container runtime PULL the image?     ← check ACR auth
2. Does the Python process START at all?         ← check with ALIVE diagnostics
3. Do imports succeed?                           ← check import chain
4. Does the server BIND to the port?             ← check uvicorn startup
5. Do health probes return 200?                  ← check /liveness + /readiness
6. Does the first real request succeed?          ← check RBAC / credentials
```

Every level of this hierarchy is invisible until you instrument it. In our case, all v9–v16
failures were at **level 1** — the image was never pulled — but the error looked exactly
like levels 3–5.

---

## 2. ACR Pull Authentication (THE Root Cause)

**Lesson:** Foundry Hosted Agent containers fail silently if they cannot pull the image.
You get zero Python output in the logstream, `container_state: Waiting`, and eventually
`ActivationFailed` — indistinguishable from a Python crash.

### What went wrong
- ACR was `Basic` tier with `anonymousPullEnabled: false` (the default)
- AcrPull was assigned to the wrong identity (`cloudparitybotproject-resource` user-assigned MI,
  not the `Microsoft.CognitiveServices` resource's **system-assigned** MI)

### The fix
```bash
# 1. Upgrade to Standard (required for anonymous pull)
az acr update --name <registry> --sku Standard

# 2. Enable anonymous pull (simplest — no credential management)
az acr update --name <registry> --anonymous-pull-enabled true

# Alternatively: grant AcrPull to the SYSTEM-ASSIGNED MI of the Foundry resource
# (not the user-assigned MI, not the project workload MI)
az cognitiveservices account show --name <foundry-resource> --resource-group <rg> \
  --query "identity.principalId" -o tsv
# → use that principalId for the role assignment
az role assignment create --assignee-object-id <system-mi-principal-id> \
  --role AcrPull --scope <acr-resource-id>
```

### Add to spec/copilot-instructions
```
ACR must have anonymousPullEnabled=true (Standard tier), OR the Foundry resource's
system-assigned managed identity must have AcrPull on the registry.
```

---

## 3. The Three Managed Identities

A Foundry Hosted Agent deployment involves **three distinct managed identities**. Each one
needs different RBAC permissions:

| Identity | Where it appears | Purpose | Required roles |
|----------|-----------------|---------|----------------|
| **Foundry resource system MI** | `az cognitiveservices account show ... --query identity.principalId` | Pulls container images from ACR | `AcrPull` on ACR |
| **`cloudparitybotproject-resource` MI** | User-assigned MI, same name as the resource | Administrative / provisioning | Owner / Contributor on resource group |
| **Project workload MI** (`<account>/projects/<project>`) | Runs Python code inside the container | Makes OpenAI API calls | `Cognitive Services OpenAI User` + `Azure AI User` on AI Services resource |

Granting roles to the wrong identity produces silent failures that look identical
to application errors.

---

## 4. `from __future__ import annotations` Breaks Agent Framework

**Lesson:** **Never add `from __future__ import annotations` to any file that defines
`@handler`-decorated methods.**

The `agent_framework` `@handler` decorator validates parameter type annotations **at
import time**. `from __future__ import annotations` converts all annotations to lazy
strings (PEP 563), causing the runtime type-check to fail with:

```
ValueError: Handler parameter 'ctx' must be annotated as WorkflowContext...
```

This crashes the Python process before it writes a single byte to stdout.

### Rules
```python
# ✅ Safe — main.py doesn't define @handler methods
# main.py: NO from __future__ import annotations needed

# ✅ Safe — workflow.py only wires executors, no @handler methods
# workflow.py: from __future__ import annotations is fine

# ❌ FATAL — executors.py defines every @handler method in the pipeline
# executors.py: NEVER add from __future__ import annotations
```

Also: always subscript `WorkflowContext` — use `WorkflowContext[dict]`, not bare `WorkflowContext`.

---

## 5. Pre-Import ALIVE Diagnostics Are Non-Negotiable

**Lesson:** Any container entry point should emit raw stdout/stderr bytes **before** any
third-party import. This lets you distinguish:

- `ALIVE1` missing → container runtime issue (pull failure, OOM, wrong entrypoint)
- `ALIVE1–4` present, `ALIVE10` missing → Python crash during import
- `ALIVE1–10` present, no server startup → startup blocking (IMDS, SDK init)
- Server started, liveness 200, but bad responses → RBAC / credential issue

```python
# At the very top of main.py — BEFORE any third-party imports
import sys, os

sys.stdout.write("ALIVE1 – Python started\n"); sys.stdout.flush()
sys.stderr.write("ALIVE1 – Python started\n"); sys.stderr.flush()
```

The `PYTHONUNBUFFERED=1` Dockerfile env var ensures these appear immediately in the
container logstream even if the process exits milliseconds later.

---

## 6. Foundry SDK Environment Variables That Trigger Blocking Calls

Two environment variables cause the `azure-ai-agentserver` SDK to make **synchronous
blocking HTTP calls** before the asyncio event loop starts. In a container both hang
for 60–120 seconds, causing liveness probe failures.

| Env var | What it triggers | Fix |
|---------|-----------------|-----|
| `APPLICATIONINSIGHTS_CONNECTION_STRING` (unset) | `AIProjectClient.telemetry.get_application_insights_connection_string()` — blocks 60-120s | Set to any non-empty string: `"skip-telemetry"` |
| `AZURE_AI_PROJECT_ENDPOINT` (set to real URL) | `_setup_tracing_with_azure_ai_client()` calls `DefaultAzureCredential().get_token()` synchronously | Set to `""` (empty = falsy, skips tracing setup) |

Always set both in `deploy_agent.py` environment variables:

```python
"APPLICATIONINSIGHTS_CONNECTION_STRING": "skip-telemetry",
"AZURE_AI_PROJECT_ENDPOINT": "",
```

---

## 7. The Foundry REST API Is More Informative Than the Portal

The Foundry playground shows a generic timeout. The REST API shows the real state:

```bash
# Get full container status
GET {project_endpoint}/agents/{name}/versions/{version}/containers/default
    ?api-version=2025-11-15-preview

# Stream container logs
GET {project_endpoint}/agents/{name}/versions/{version}/containers/default:logstream
    ?api-version=2025-11-15-preview

# Useful fields: container.state, container.health_state, container.replicas[*].container_state
# Key states: Activating → RunningAtMaxScale (healthy) or ActivationFailed (dead)
```

Foundry routes requests with a 100-second `HttpClient.Timeout`. If the container is
`ActivationFailed`, Foundry returns `status: failed, error: client_disconnect` at
exactly ~101s — this looks like an application timeout but is actually a pull/start failure.

---

## 8. `min_replicas: 1` Prevents Cold Starts

The default `min_replicas` is `0` (scale-to-zero). This causes a cold start on every
request, which adds 30–120 seconds — exceeding Foundry's 30-second playground timeout.

The `az cognitiveservices agent start` CLI doesn't expose `min_replicas`. Use the REST API:

```python
body = json.dumps({"min_replicas": 1}).encode()
httpx.post(f"{project_endpoint}/agents/{name}/versions/{version}/containers/default:start"
           + "?api-version=2025-11-15-preview",
           content=body, headers={...})
```

---

## 9. Spec-Driven Vibe Coding: What to Include in the Spec

The AI coding agent will implement exactly what the spec asks for — and silently skip
what it doesn't mention. These items **must be in the spec / copilot-instructions.md**
to avoid repeated debugging cycles:

### Infrastructure prerequisites (add to spec)
```markdown
## Deployment Prerequisites
- ACR: Standard tier, anonymousPullEnabled=true
- RBAC on AI Services resource:
  - Project workload MI needs: Cognitive Services OpenAI User, Azure AI User
  - Foundry system MI needs: AcrPull on ACR
- Container env vars: APPLICATIONINSIGHTS_CONNECTION_STRING=skip-telemetry,
  AZURE_AI_PROJECT_ENDPOINT="" (empty)
- Deployment: min_replicas=1 via REST API (not az CLI)
```

### Code constraints (add to spec)
```markdown
## Code Constraints
- Files with @handler methods MUST NOT have `from __future__ import annotations`
- WorkflowContext annotations must always be subscripted: WorkflowContext[dict]
- main.py must emit ALIVE diagnostics before any third-party import
- Server startup must not block on credential warm-up: use asyncio.wait_for(..., timeout=10)
```

### Debugging runbook (add to spec)
```markdown
## Debugging Deployed Containers
1. Check container state via REST API: .../containers/default?api-version=...
2. Stream logs: .../containers/default:logstream?api-version=...
3. If no ALIVE lines → check ACR pull auth / container_state in replicas[]
4. If ALIVE1–4 but no server → look for import errors, blocking SDK calls
5. If server running but 401 → check RBAC on project workload MI
```

---

## 10. Incremental Verification Beats Big-Bang Deploy

Each deploy iteration took 5–15 minutes (build + deploy + wait for health). Iteration
speed was the biggest productivity constraint.

### What helps
- **Local smoke test before every build**: `python main.py` with container env vars set
- **Check ACR pull separately**: `az acr repository show-tags` + anonymous curl test
- **Use a minimal test image first**: a 5-line Python HTTP server reveals logstream/auth
  issues in <2 minutes without the 3-minute full pip install
- **Keep `check_status.py`** as a permanent regression test against the live deployment
- **Structured deployment script** (`infra/deploy_agent.py`) that always sets the correct
  env vars, versions, and `min_replicas` — no manual az CLI incantations

---

## Quick Reference: Deployment Checklist

```
□ ACR: Standard tier, anonymousPullEnabled=true
□ ACR: AcrPull → Foundry resource system-assigned MI  (or anonymous pull)
□ AI Services: Cognitive Services OpenAI User → project workload MI
□ AI Services: Azure AI User → project workload MI
□ deploy_agent.py: APPLICATIONINSIGHTS_CONNECTION_STRING=skip-telemetry
□ deploy_agent.py: AZURE_AI_PROJECT_ENDPOINT="" (empty string)
□ deploy_agent.py: SKIP_SCRAPING=true (if outbound internet blocked)
□ deploy_agent.py: min_replicas=1 via REST API start endpoint
□ Dockerfile: ENV PYTHONUNBUFFERED=1
□ main.py: ALIVE1 diagnostic before any third-party import
□ executors.py: NO from __future__ import annotations
□ executors.py: WorkflowContext[dict] (subscripted, not bare)
□ Local test: python main.py with SKIP_SCRAPING=true before every acr build
□ Post-deploy: python check_status.py to verify live response
```
