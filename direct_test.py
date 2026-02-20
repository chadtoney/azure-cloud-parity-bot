"""Direct REST API call to Foundry responses endpoint — bypasses SDK to check raw latency."""
import time
import json
import urllib.request
from azure.identity import DefaultAzureCredential

PROJECT_ENDPOINT = "https://cloudparitybotproject-resource.services.ai.azure.com/api/projects/cloudparitybotproject"
AGENT_NAME = "azure-cloud-parity-bot"
AGENT_VERSION = "11"

cred = DefaultAzureCredential()
token = cred.get_token("https://ai.azure.com/.default").token

body = json.dumps({
    "input": [{"role": "user", "content": "Say hello"}],
    "agent": {"name": AGENT_NAME, "version": AGENT_VERSION, "type": "agent_reference"},
}).encode()

url = f"{PROJECT_ENDPOINT}/openai/responses?api-version=2025-11-15-preview"
req = urllib.request.Request(
    url,
    data=body,
    method="POST",
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
)

print(f"Sending direct REST call to: {url}")
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=150) as resp:
        data = resp.read()
        elapsed = time.time() - t0
        print(f"SUCCESS in {elapsed:.1f}s")
        result = json.loads(data)
        print("output_text:", repr(result.get("output_text", ""))[:300])
        print("status:", result.get("status"))
except Exception as e:
    print(f"ERROR after {time.time()-t0:.1f}s: {type(e).__name__}: {e}")
