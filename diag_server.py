#!/usr/bin/env python3
"""
diag_server.py – Diagnostic HTTP server for Foundry container troubleshooting.

Implements POST /responses (OpenAI Responses API, protocol v1) but instead of
running the parity agent it immediately streams a diagnostic report:

  1. Environment variable presence check
  2. DefaultAzureCredential.get_token() timing (10 s timeout)
  3. Azure OpenAI endpoint reachability (5 s timeout, uses token from step 2)

Deploy this as a separate Foundry agent, send ANY message, and get back a
diagnostic report within ~15 s – or hard timeout info if something is stuck.
Comparing this against the real bot tells us exactly which layer is broken.

Usage (local):
    SKIP_SCRAPING=true python diag_server.py
    curl -s -X POST http://localhost:8088/responses \
         -H "Content-Type: application/json" \
         -d '{"input":"go","stream":true}'  | cat
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Generator

PORT = 8088
RESPONSE_ID = "resp_diag"
ITEM_ID     = "item_diag"


# ---------------------------------------------------------------------------
# SSE / Responses-API helpers
# ---------------------------------------------------------------------------

def _event(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()


def _delta(text: str) -> bytes:
    return _event("response.output_text.delta", {
        "type":          "response.output_text.delta",
        "item_id":       ITEM_ID,
        "output_index":  0,
        "content_index": 0,
        "delta":         text,
    })


def _stream_prefix() -> list[bytes]:
    return [
        _event("response.created", {
            "type": "response.created",
            "response": {"id": RESPONSE_ID, "object": "response",
                         "status": "in_progress", "output": []},
        }),
        _event("response.output_item.added", {
            "type":         "response.output_item.added",
            "output_index": 0,
            "item": {"id": ITEM_ID, "type": "message",
                     "status": "in_progress", "role": "assistant", "content": []},
        }),
        _event("response.content_part.added", {
            "type":          "response.content_part.added",
            "item_id":        ITEM_ID,
            "output_index":  0,
            "content_index": 0,
            "part":          {"type": "text", "text": ""},
        }),
    ]


def _stream_suffix(full_text: str) -> list[bytes]:
    return [
        _event("response.output_text.done", {
            "type":          "response.output_text.done",
            "item_id":        ITEM_ID,
            "output_index":  0,
            "content_index": 0,
            "text":          full_text,
        }),
        _event("response.output_item.done", {
            "type":         "response.output_item.done",
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
# Diagnostic checks (each run in a thread with a hard timeout)
# ---------------------------------------------------------------------------

def _check_env() -> Generator[str, None, None]:
    yield "## Environment Variables\n\n"
    for var in [
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
    ]:
        val = os.environ.get(var)
        if val:
            display = val if len(val) <= 60 else val[:57] + "..."
            yield f"- `{var}` = `{display}` ✅\n"
        else:
            yield f"- `{var}` *(not set)*\n"
    yield "\n"


def _acquire_token(timeout_s: float = 10.0):
    """Returns (elapsed, token_or_None, error_str_or_None)."""
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
        fut = ex.submit(
            cred.get_token,
            "https://cognitiveservices.azure.com/.default"
        )
        try:
            tok = fut.result(timeout=timeout_s)
            return time.monotonic() - t0, tok, None
        except concurrent.futures.TimeoutError:
            return time.monotonic() - t0, None, f"TIMEOUT after {timeout_s:.0f}s"
        except Exception as exc:
            return time.monotonic() - t0, None, str(exc)



def _check_openai(token_value: str | None) -> Generator[str, None, None]:
    import httpx
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    deployment = os.environ.get("FAST_AZURE_OPENAI_DEPLOYMENT") \
              or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    api_ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

    yield "## OpenAI Endpoint Reachability\n\n"

    if not endpoint:
        yield "❌ `AZURE_OPENAI_ENDPOINT` not set — skipping\n\n"
        return

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_ver}"
    yield f"POST `{url}` (5 s timeout)…\n\n"

    headers = {"Content-Type": "application/json"}
    if token_value:
        headers["Authorization"] = f"Bearer {token_value}"
    else:
        yield "⚠️  No token available — request will likely fail with 401\n\n"

    body = {"messages": [{"role": "user", "content": "say ok"}], "max_tokens": 5}

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            r = client.post(url, headers=headers, json=body)
        elapsed = time.monotonic() - t0
        if r.status_code == 200:
            yield f"✅ **HTTP {r.status_code} in {elapsed:.2f}s** — OpenAI reachable\n\n"
        else:
            # Non-2xx but got a response → network works, auth/config issue
            snippet = r.text[:200].replace("\n", " ")
            yield f"⚠️  **HTTP {r.status_code} in {elapsed:.2f}s** (network OK, check auth/deployment)\n`{snippet}`\n\n"
    except httpx.TimeoutException:
        elapsed = time.monotonic() - t0
        yield f"❌ **Timeout after {elapsed:.2f}s** — endpoint unreachable from container\n\n"
    except Exception as exc:
        elapsed = time.monotonic() - t0
        yield f"❌ **Error in {elapsed:.2f}s**: `{exc}`\n\n"


def build_diagnostic_report() -> Generator[bytes, None, None]:
    """Yield SSE-formatted bytes for the full diagnostic report."""
    for chunk in _stream_prefix():
        yield chunk

    full_text_parts: list[str] = []

    def emit(text: str) -> bytes:
        full_text_parts.append(text)
        return _delta(text)

    # Header (instant)
    yield emit("# Parity Bot Container Diagnostic\n\n")
    yield emit(f"**Server time:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
    yield emit(f"**Python:** {sys.version.split()[0]}  **PID:** {os.getpid()}\n\n")

    # Env vars (instant)
    for line in _check_env():
        yield emit(line)

    # Credential check — run ONCE, emit status, keep token for OpenAI check.
    yield emit("## Managed Identity / Credential Check\n\n")
    yield emit("Calling `DefaultAzureCredential.get_token()` (10 s hard timeout)…\n\n")
    elapsed, tok, err = _acquire_token(timeout_s=10.0)
    token_value: str | None = None
    if tok:
        token_value = tok.token
        yield emit(f"✅ **Token acquired in {elapsed:.2f}s** (expires {tok.expires_on})\n\n")
    else:
        yield emit(f"❌ **Failed in {elapsed:.2f}s**: `{err}`\n\n")
        yield emit("*(Credential probe failure is the most common cause of 30s timeouts)*\n\n")

    # OpenAI check (blocks up to 5s)
    for line in _check_openai(token_value):
        yield emit(line)

    yield emit("---\n*Diagnostic complete.*\n")

    full_text = "".join(full_text_parts)
    for chunk in _stream_suffix(full_text):
        yield chunk


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DiagHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default noisy logging
        print(f"[diag] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self):
        body = json.dumps({"status": "ready", "version": "diag-1",
                           "server_time": time.time()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Consume request body (required before writing response)
        length = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(length) if length else b""

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        try:
            for chunk in build_diagnostic_report():
                # HTTP chunked encoding: hex-length CRLF data CRLF
                self.wfile.write(f"{len(chunk):x}\r\n".encode())
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            # Final chunk (end of chunked body)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except BrokenPipeError:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"=== Parity Bot Diagnostic Server starting on port {PORT} ===", flush=True)
    server = HTTPServer(("0.0.0.0", PORT), DiagHandler)
    print(f"Listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()
