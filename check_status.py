"""Check v8 agent status and test a live request."""
import json
import time
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import AgentReference
from azure.identity import DefaultAzureCredential

PROJECT_ENDPOINT = "https://cloudparitybotproject-resource.services.ai.azure.com/api/projects/cloudparitybotproject"
client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())

# --- test a live request ---
print("Sending test request to azure-cloud-parity-bot v8 ...")
t0 = time.time()
try:
    oc = client.get_openai_client()
    resp = oc.responses.create(
        input=[{"role": "user", "content": "Say hello and confirm you are the Azure Cloud Parity Bot."}],
        extra_body={"agent": AgentReference(name="azure-cloud-parity-bot", version="8").as_dict()},
        timeout=60,
    )
    print(f"SUCCESS in {time.time() - t0:.1f}s")
    print(resp.output_text[:300])
except Exception as e:
    print(f"ERROR after {time.time() - t0:.1f}s: {type(e).__name__}: {e}")

