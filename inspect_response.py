"""Inspect the full response structure from the hosted agent."""
import time
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import AgentReference
from azure.identity import DefaultAzureCredential

PROJECT_ENDPOINT = "https://cloudparitybotproject-resource.services.ai.azure.com/api/projects/cloudparitybotproject"
client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
oc = client.get_openai_client()

t0 = time.time()
resp = oc.responses.create(
    input=[{"role": "user", "content": "Say hello"}],
    extra_body={"agent": AgentReference(name="azure-cloud-parity-bot", version="9").as_dict()},
    timeout=150,
)
print(f"done in {time.time()-t0:.1f}s")
print("output_text repr:", repr(resp.output_text)[:300])
print("output count:", len(resp.output) if resp.output else 0)
for i, item in enumerate(resp.output or []):
    item_type = getattr(item, "type", "?")
    print(f"  output[{i}]: type={item_type}")
    if hasattr(item, "content"):
        for j, c in enumerate(item.content or []):
            print(f"    content[{j}]: type={getattr(c,'type','?')} text={repr(getattr(c,'text','?'))[:100]}")
    if hasattr(item, "text"):
        print(f"  text: {repr(item.text)[:200]}")
