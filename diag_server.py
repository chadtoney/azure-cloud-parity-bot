#!/usr/bin/env python3
"""
diag_server.py - Diagnostic server using the same uvicorn/Starlette stack as
the real Azure AI Agent Server framework.

Streams a health-check report (env vars, credential timing, OpenAI reachability)
immediately upon any POST /responses request - no agent framework overhead.

If this times out in Foundry, the problem is in the platform routing/networking.
If this works but the real bot does not, the problem is in our agent code.

Usage:
    pip install azure-identity httpx uvicorn starlette
    python diag_server.py       # listens on DEFAULT_AD_PORT (default 8088)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import time
from typing import AsyncGenerator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

PORT = int(os.environ.get("DEFAULT_AD_PORT", 8088))
RESPONSE_ID = "resp_diag"
ITEM_ID = "item_diag"


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _delta(text: str) -> str:
    return _event("response.output_text.delta", {
        "type": "response.output_text.delta",
        "item_id": ITEM_ID,
        "output_index": 0,
        "content_index": 0,
        "delta": text,
    })


def _stream_prefix() -> list[str]:
    return [
        _event("response.created", {
            "type": "response.created",
            "response": {"id": RESPONSE_ID, "object": "response",
                         "status": "in_progress", "output": []},
        }),
        _event("response.output_item.added", {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"id": ITEM_ID, "type": "message",
                     "status": "in_progress", "role": "assistant", "content": []},
        }),
        _event("response.content_part.added", {
            "type": "response.content_part.added",
            "item_id": ITEM_ID,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "text", "text": ""},
        }),
    ]


def _stream_suffix(full_text: str) -> list[str]:
    return [
        _event("response.output_text.done", {
            "type": "response.output_text.done",
            "item_id": ITEM_ID,
            "output_index": 0,
            "content_index": 0,
            "text": full_text,
        }),
        _event("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"id": ITEM_ID, "type": "message",
                     "status": "completed", "role": "assistant",
                     "content": [{"type": "text", "text": full_text}]},
        }),
        _event("response.done", {
            "type": "response.done",
            "response": {"id": RESPONSE_ID, "object": "response",
                         "status": "completed",
                         "output": [{"id": ITEM_ID, "type": "message",
                                     "role": "assistant",
                                     "content": [{"type": "text", "text": full_text}]}]},
        }),
    ]


# ---------------------------------------------------------------------------
# Diagnostic logic - all I/O runs in thread pool to avoid blocking event loop
# ---------------------------------------------------------------------------

_ENV_VARS_TO_CHECK = [
    "DEFAULT_AD_PORT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
    "FAST_AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
    "SKIP_SCRAPING",
    "AGENT_PROJECT_RESOURCE_ID",
    "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "AZURE_FEDERATED_TOKEN_FILE",
    "IMDS_ENDPOINT",
    "MSI_ENDPOINT",
    "WEBSITE_HOSTNAME",
    "CONTAINER_APP_HOSTNAME",
]


def _acquire_token_sync(timeout_s: float = 12.0):
    from azure.identity import DefaultAzureCredential
    cred = DefaultAzureCredential(
        exclude_interactive_browser_credential=True,
        exclude_shared_token_cache_credential=True,
        exclude_visual_studio_code_credential=True,
        exclude_powershell_credential=True,
        exclude_developer_cli_credential=True,
    )
    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(cred.get_token, "https://cognitiveservices.azure.com/.default")
        try:
            tok = fut.result(timeout=timeout_s)
            return time.monotonic() - t0, tok, None
        except concurrent.futures.TimeoutError:
            return time.monotonic() - t0, None, f"TIMEOUT after {timeout_s:.0f}s"
        except Exception as exc:
            return time.monotonic() - t0, None, str(exc)


def _check_openai_sync(token_value: str | None) -> str:
    import httpx
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    deployment = (os.environ.get("FAST_AZURE_OPENAI_DEPLOYMENT")
                  or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"))
    api_ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    if not endpoint:
        return "- AZURE_OPENAI_ENDPOINT not set - skip\n"
    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_ver}"
    headers = {"Content-Type": "application/json"}
    if token_value:
        headers["Authorization"] = f"Bearer {token_value}"
    body = {"messages": [{"role": "user", "content": "say ok"}], "max_tokens": 5}
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            r = client.post(url, headers=headers, json=body)
        elapsed = time.monotonic() - t0
        if r.status_code == 200:
            return f"OK HTTP {r.status_code} in {elapsed:.2f}s - OpenAI reachable\n"
        else:
            snippet = r.text[:300].replace("\n", " ")
            return f"WARN HTTP {r.status_code} in {elapsed:.2f}s - {snippet}\n"
    except httpx.TimeoutException:
        return f"FAIL Timeout after {time.monotonic()-t0:.2f}s - endpoint unreachable from container\n"
    except Exception as exc:
        return f"FAIL Error in {time.monotonic()-t0:.2f}s: {exc}\n"


async def _diagnostic_stream() -> AsyncGenerator[str, None]:
    parts: list[str] = []

    def emit(text: str) -> str:
        parts.append(text)
        return _delta(text)

    for e in _stream_prefix():
        yield e

    # Header - arrives within milliseconds so Foundry sees TTFB immediately
    yield emit("# Parity Bot Container Diagnostic\n\n")
    yield emit(f"Listening port (DEFAULT_AD_PORT): {PORT}\n")
    yield emit(f"Server time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
    yield emit(f"Python: {sys.version.split()[0]}  PID: {os.getpid()}\n\n")

    yield emit("## Environment Variables\n\n")
    for var in _ENV_VARS_TO_CHECK:
        val = os.environ.get(var)
        if val:
            display = val if len(val) <= 60 else val[:57] + "..."
            yield emit(f"- {var} = {display}\n")
        else:
            yield emit(f"- {var}: NOT SET\n")
    yield emit("\n")

    # Yield to event loop so the env-var chunk flushes before blocking on I/O
    await asyncio.sleep(0)

    yield emit("## Credential Check (12s timeout)\n\n")
    loop = asyncio.get_event_loop()
    elapsed, tok, err = await loop.run_in_executor(None, _acquire_token_sync, 12.0)
    token_value: str | None = None
    if tok:
        token_value = tok.token
        yield emit(f"OK Token acquired in {elapsed:.2f}s (expires {tok.expires_on})\n\n")
    else:
        yield emit(f"FAIL Failed in {elapsed:.2f}s: {err}\n\n")
        yield emit("NOTE: Credential failure is the most common cause of 30s Foundry timeouts\n\n")

    yield emit("## OpenAI Endpoint Reachability (5s timeout)\n\n")
    result = await loop.run_in_executor(None, _check_openai_sync, token_value)
    yield emit(result)
    yield emit("\n---\nDiagnostic complete.\n")

    full_text = "".join(parts)
    for e in _stream_suffix(full_text):
        yield e


# ---------------------------------------------------------------------------
# Starlette app
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ready",
        "version": "diag-2",
        "port": PORT,
        "server_time": time.time(),
    })


async def responses_endpoint(request: Request) -> Response:
    return StreamingResponse(
        _diagnostic_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app = Starlette(routes=[
    Route("/",          health,             methods=["GET"]),
    Route("/health",    health,             methods=["GET"]),
    Route("/responses", responses_endpoint, methods=["POST"]),
])


if __name__ == "__main__":
    print(f"=== Parity Bot Diagnostic Server (uvicorn) port={PORT} ===", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, loop="asyncio")
