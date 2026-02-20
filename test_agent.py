"""
test_agent.py – Calls the local agent HTTP server and measures timing.

Start the server first:
    SKIP_SCRAPING=true python main.py

Then run:
    python test_agent.py [--query "..."] [--runs N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

SERVER = "http://localhost:8088"
DEFAULT_QUERY = "Check Azure Kubernetes Service government parity"


def call_agent(query: str, run_label: str = "") -> tuple[float, float, int]:
    """
    POST /responses (streaming) to the local agent server.
    Returns (http_200_elapsed, ttfb, content_chars).
    """
    body = {"model": "parity-bot", "input": query, "stream": True}
    start = time.perf_counter()
    http_elapsed = None
    ttfb = None
    content_chars = 0
    duplicates: dict[str, int] = {}
    full_text: list[str] = []

    with httpx.Client(timeout=httpx.Timeout(60.0)) as c:
        with c.stream(
            "POST",
            f"{SERVER}/responses",
            json=body,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        ) as resp:
            http_elapsed = time.perf_counter() - start
            if resp.status_code != 200:
                body_bytes = resp.read()
                print(f"  HTTP {resp.status_code}: {body_bytes.decode()[:300]}")
                return http_elapsed, 0.0, 0

            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    evt = json.loads(data)
                    t = evt.get("type", "")
                    delta = evt.get("delta", "") or ""
                    if isinstance(delta, dict):
                        delta = delta.get("text", "")
                    if delta:
                        if ttfb is None:
                            ttfb = time.perf_counter() - start
                        full_text.append(delta)
                        content_chars += len(delta)
                        # Detect duplicate content
                        h = hash(delta[:40])
                        duplicates[h] = duplicates.get(h, 0) + 1
                except json.JSONDecodeError:
                    pass

    total = time.perf_counter() - start
    text = "".join(full_text)

    # Check for obvious duplication: same paragraph appearing twice
    mid = len(text) // 2
    is_dup = len(text) > 200 and text[:mid].strip() == text[mid:].strip()

    label = f"[{run_label}] " if run_label else ""
    print(f"  {label}HTTP 200 in {http_elapsed:.2f}s  TTFB {ttfb or 0:.2f}s  "
          f"Total {total:.2f}s  Chars {content_chars}"
          + ("  ⚠️ DUPLICATE DETECTED" if is_dup else "  ✅ clean"))

    if content_chars < 100:
        print(f"  Response: {text[:300]}")

    return http_elapsed, ttfb or 0.0, content_chars


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--runs", type=int, default=2, help="Number of back-to-back calls")
    args = parser.parse_args()

    # Readiness check
    try:
        r = httpx.get(f"{SERVER}/readiness", timeout=3)
        print(f"Readiness: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Server not reachable: {e}")
        sys.exit(1)

    print(f"\nQuery: {args.query!r}\n{'─'*60}")
    for i in range(1, args.runs + 1):
        print(f"Run {i}:")
        call_agent(args.query, run_label=f"run{i}")
        if i < args.runs:
            time.sleep(1)

    print("\nDone.")


if __name__ == "__main__":
    main()

