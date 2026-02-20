# Operations Guide — Cost, Security & Reliability

## Cost Optimization

### 1. Token usage — biggest lever
- **Cache parity results**: `data/features/` stores results but there's no cache-hit check before invoking the pipeline. Add a TTL check (e.g. 24h) in `ParityStarterExecutor` to skip the LLM pipeline entirely for recently-answered queries.
- **Use `gpt-4o-mini` everywhere except final report**: `FAST_AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini` is already used for extraction, but `gpt-4o` is still used for comparison and report steps. Those can be downgraded.
- **Reduce `AGENT_MAX_ITERATIONS`**: Default is 10. Parity lookups rarely need more than 5 hops — set `AGENT_MAX_ITERATIONS=5` in `.env`.

### 2. Foundry Hosted Agent compute
- `min_replicas=1` keeps the container warm 24/7 to meet the 30s Foundry timeout. If async/webhook delivery is acceptable, drop to `min_replicas=0` to pay only when the agent is active.
- No native schedule-based scale-down yet in Foundry — a daily `az` CLI job could stop/start the container outside business hours.

### 3. ACR tier
- Currently Standard (~$20/month) for `anonymousPullEnabled=true`. After disabling anonymous pull and using the Foundry system MI's `AcrPull` assignment instead, downgrade to Basic (~$5/month):
  ```bash
  az acr update --name cloudparitybotreg --sku Basic --anonymous-pull-enabled false
  ```

### 4. Build efficiency
- Add a `.dockerignore` to exclude `__pycache__/`, `.venv/`, `logs/`, `data/`, `reports/` from the ACR build context — reduces upload size and build time.

---

## Security

### Priority 1 — Disable ACR anonymous pull (do now)
Anonymous pull is currently enabled, meaning anyone can pull the container image without credentials. The Foundry system MI already has `AcrPull` — anonymous pull is not needed:
```bash
az acr update --name cloudparitybotreg --sku Basic --anonymous-pull-enabled false
```

### Priority 2 — Disable public network access on AI Services
The endpoint `cloudparitybotproject-resource.cognitiveservices.azure.com` is publicly reachable with any valid Entra token. Restrict to the Foundry VNet via private endpoint:
```bash
az cognitiveservices account update \
  --name cloudparitybotproject-resource \
  --resource-group Team2 \
  --public-network-access Disabled
```
Then add a private endpoint from the Foundry managed VNet to the AI Services resource.

### Priority 3 — Move env vars to Key Vault
Per Microsoft's hosted agent security guidance: *"Don't put secrets in container images or environment variables. Use managed identities and connections, and store secrets in a managed secret store."*

Currently `infra/deploy_agent.py` sets `AGENT_PROJECT_RESOURCE_ID` and other config as plain container env vars. Use a Foundry Key Vault connection instead:
1. Create a Key Vault in the `Team2` resource group
2. Grant the project workload MI `Key Vault Secrets User`
3. Add a Key Vault connection in the Foundry portal (Project → Settings → Connections)
4. Reference secrets via the connection in `deploy_agent.py`

### Priority 4 — Verify Prompt Shields coverage (content filtering partially in place)
`Microsoft.DefaultV2` content filter policy is already applied to both `gpt-4o` and `gpt-4o-mini` deployments — harmful category filtering on input and output is covered.

**Remaining gap — indirect prompt injection**: The web scraper feeds raw HTML/text from third-party pages directly into the LLM context. `Microsoft.DefaultV2` filters harmful *outputs* but doesn't detect injected instructions embedded in scraped content (e.g. a malicious Azure docs page containing `"Ignore previous instructions..."`).

To close this: enable **Prompt Shields for indirect attacks** (also called groundedness / indirect injection detection) on the model deployment in the Foundry portal — this is a separate toggle from the default content filter.

### Priority 5 — Private networking (future)
Microsoft has a "Standard Setup with private networking" Bicep template that puts the entire agent behind a VNet with private endpoints for Foundry, AI Search, Storage, and Cosmos DB — no public egress. **Current limitation**: hosted agents (new Foundry) don't support this yet (preview restriction). Track for GA.

### Priority 6 — Microsoft Defender for Cloud
Enable the CSPM plan on the subscription to auto-discover the AI workload, surface public endpoint exposure, and generate an AI Bill of Materials:
```bash
az security pricing create --name CloudPosture --tier Standard
```

### What's already good
| Control | Status |
|---------|--------|
| No API keys — Entra ID auth throughout | ✅ |
| Managed identity for container runtime | ✅ |
| RBAC scoped to least privilege | ✅ |
| No secrets in container image | ✅ |
| `PYTHONUNBUFFERED=1` — no sensitive data buffered | ✅ |
| Content filtering (Microsoft.DefaultV2) on gpt-4o + gpt-4o-mini | ✅ |

---

## Reliability

### 1. `min_replicas=1` — already in place
Prevents the ~60–120s cold start that exceeds Foundry's 30s timeout. Must be set via the REST API (az CLI doesn't expose this parameter) — handled in `infra/deploy_agent.py`.

### 2. Retry logic in client layer
`config/settings.py` has `scrape_max_retries=3` and `scrape_delay_seconds=1.0`. Ensure the `WebClient` and `MsLearnClient` actually respect these, and use **exponential backoff** (not fixed delay) for transient HTTP 429/503 responses from docs pages.

### 3. `asyncio.wait_for` timeouts on all credential/warm-up calls
Already applied to `warm_feature_extractor_credential()`. Apply the same pattern to any other startup-time Azure SDK calls to prevent blocking uvicorn if IMDS is slow.

### 4. Health probe endpoint returns structured status
Currently `/liveness` and `/readiness` return 200 OK unconditionally (provided by the agentserver SDK). Consider adding a `/health` route that validates the OpenAI credential is warm and returns a degraded state if not — gives Foundry a signal to restart the container rather than serve broken requests.

### 5. Log retention
Logs go to `logs/parity-bot.log` inside the container, which is ephemeral — lost on container restart. Wire `loguru` to also emit to stdout (already done via `PYTHONUNBUFFERED=1`) and connect the Foundry resource to a Log Analytics workspace for persistent log storage:
```bash
az monitor diagnostic-settings create \
  --name parity-bot-logs \
  --resource <ai-services-resource-id> \
  --workspace <log-analytics-workspace-id> \
  --logs '[{"category":"Audit","enabled":true}]'
```

### 6. `SKIP_SCRAPING=true` fallback
If the Foundry container runtime blocks outbound internet (common in secure environments), the pipeline falls back to LLM training knowledge automatically. This is a reliability feature — document it as intentional behavior, not a degraded mode.

---

## Quick Reference Checklist

```
Security
□ ACR: disable anonymous pull, downgrade to Basic tier
□ AI Services: public network access → Disabled + private endpoint
□ Key Vault: move AGENT_PROJECT_RESOURCE_ID and config out of env vars
□ Prompt Shields (indirect injection): enable on model deployment in Foundry portal
□ Content filtering: ✅ Microsoft.DefaultV2 already applied to gpt-4o + gpt-4o-mini
□ Defender for Cloud: enable CSPM plan on subscription

Cost
□ Cache-hit check in ParityStarterExecutor (skip pipeline if result < 24h old)
□ AGENT_MAX_ITERATIONS=5 in .env
□ ACR downgrade to Basic after disabling anonymous pull
□ Add .dockerignore to exclude .venv, __pycache__, logs, data

Reliability
□ Exponential backoff in WebClient / MsLearnClient retry logic
□ asyncio.wait_for on all startup Azure SDK calls
□ Log Analytics workspace connected to AI Services resource
□ min_replicas=1 maintained in every deploy_agent.py invocation
```
