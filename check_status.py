"""Check the latest agent version status and test a live request."""
import time
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import AgentReference
from azure.identity import DefaultAzureCredential

AGENT_VERSION = "18"  # update to match the currently deployed version
PROJECT_ENDPOINT = "https://cloudparitybotproject-resource.services.ai.azure.com/api/projects/cloudparitybotproject"
client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())

# --- test a live request ---
print(f"Sending test request to azure-cloud-parity-bot v{AGENT_VERSION} ...")
t0 = time.time()
try:
    oc = client.get_openai_client()
    resp = oc.responses.create(
        input=[{"role": "user", "content": "Say hello and confirm you are the Azure Cloud Parity Bot."}],
        extra_body={"agent": AgentReference(name="azure-cloud-parity-bot", version=AGENT_VERSION).as_dict()},
        timeout=120,
    )
    print(f"SUCCESS in {time.time() - t0:.1f}s")
    print(resp.output_text[:300])
except Exception as e:
    print(f"ERROR after {time.time() - t0:.1f}s: {type(e).__name__}: {e}")

