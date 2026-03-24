#!/usr/bin/env python3
"""
HTTP capture proxy for inspecting Claude Agent SDK requests/responses.

Usage:
    # Terminal 1: Start the capture proxy
    python tests/capture_proxy.py --target https://api.anthropic.com --port 8888

    # Terminal 2: Run your test pointing to the proxy
    ANTHROPIC_BASE_URL=http://localhost:8888 python tests/long_test.py --official

    # Captured data is saved to tests/captures/ directory as JSON files.
    # Each request/response pair is saved with a timestamp filename.

Options:
    --target    Target API base URL (default: https://api.anthropic.com)
    --port      Local proxy port (default: 8888)
    --no-file   Don't save to files, only print to console
    --body-limit  Max body chars to print to console (default: 2000, 0=unlimited)
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn

app = FastAPI(title="Claude SDK Capture Proxy")

# Global config set in main()
TARGET_BASE_URL = ""
SAVE_TO_FILE = True
BODY_LIMIT = 2000
CAPTURE_DIR = Path(__file__).parent / "captures"


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, total {len(text)} chars]"


def print_separator(title: str):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")


def print_headers(headers: dict, prefix: str = "  "):
    """Print headers, masking sensitive values."""
    for k, v in sorted(headers.items()):
        display_v = v
        if k.lower() in ("x-api-key", "authorization"):
            display_v = v[:12] + "..." + v[-4:] if len(v) > 20 else "***"
        print(f"{prefix}{k}: {display_v}")


def save_capture(capture: dict, seq: int):
    if not SAVE_TO_FILE:
        return
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = CAPTURE_DIR / f"{ts}_{seq:04d}.json"
    path.write_text(json.dumps(capture, indent=2, ensure_ascii=False, default=str))
    print(f"  Saved to: {path}")


_seq = 0


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(request: Request, path: str):
    global _seq
    _seq += 1
    seq = _seq

    # --- Capture request ---
    body_bytes = await request.body()
    req_headers = dict(request.headers)
    req_body_str = body_bytes.decode("utf-8", errors="replace")

    try:
        req_body_json = json.loads(req_body_str)
    except (json.JSONDecodeError, ValueError):
        req_body_json = None

    target_url = f"{TARGET_BASE_URL}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    print_separator(f"REQUEST #{seq}  {request.method} /{path}")
    print(f"  Target: {target_url}")
    print(f"  Time:   {datetime.now(timezone.utc).isoformat()}")
    print(f"\n  --- Request Headers ---")
    print_headers(req_headers)

    print(f"\n  --- Request Body ({len(req_body_str)} chars) ---")
    if req_body_json:
        formatted = json.dumps(req_body_json, indent=2, ensure_ascii=False)
        print(truncate(formatted, BODY_LIMIT))
    else:
        print(truncate(req_body_str, BODY_LIMIT))

    # --- Forward to target ---
    fwd_headers = {
        k: v for k, v in req_headers.items()
        if k.lower() not in ("host", "transfer-encoding", "content-length")
    }

    start = time.perf_counter()

    is_stream = False
    if req_body_json and req_body_json.get("stream"):
        is_stream = True

    capture = {
        "seq": seq,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request": {
            "method": request.method,
            "url": target_url,
            "headers": {k: v for k, v in req_headers.items()
                       if k.lower() not in ("x-api-key", "authorization")},
            "body": req_body_json or req_body_str,
        },
    }

    if is_stream:
        # For streaming: we must NOT use context managers because
        # StreamingResponse consumes the generator AFTER this function returns.
        # Instead, create client/stream manually and close them when the
        # generator finishes.
        client = httpx.AsyncClient(timeout=httpx.Timeout(1800.0))
        req = client.build_request(
            method=request.method,
            url=target_url,
            headers=fwd_headers,
            content=body_bytes,
        )
        upstream = await client.send(req, stream=True)
        latency = time.perf_counter() - start
        resp_headers = dict(upstream.headers)

        print(f"\n  --- Response (STREAMING) ---")
        print(f"  Status:  {upstream.status_code}")
        print(f"  TTFB:    {latency*1000:.0f}ms")
        print(f"  --- Response Headers ---")
        print_headers(resp_headers)

        chunks = []
        # ping_count = 0

        async def stream_and_capture():
            # nonlocal ping_count
            # buffer = ""
            try:
                async for chunk in upstream.aiter_bytes():
                    chunks.append(chunk.decode("utf-8", errors="replace"))
                    yield chunk

                    # --- Ping filtering (commented out, currently pass-through) ---
                    # text = chunk.decode("utf-8", errors="replace")
                    # chunks.append(text)
                    # buffer += text
                    #
                    # while "\n\n" in buffer:
                    #     event_raw, buffer = buffer.split("\n\n", 1)
                    #     event_raw = event_raw.strip()
                    #     if not event_raw:
                    #         continue
                    #     is_ping = any(
                    #         line.strip() == "event: ping"
                    #         for line in event_raw.split("\n")
                    #     )
                    #     if is_ping:
                    #         ping_count += 1
                    #         print(f"  [ping #{ping_count} filtered]")
                    #         continue
                    #     yield (event_raw + "\n\n").encode("utf-8")
                    #
                    # if buffer.strip():
                    #     yield buffer.encode("utf-8")
            finally:
                # Close stream and client when done (or on error)
                await upstream.aclose()
                await client.aclose()

                # After stream completes, save capture
                total_time = time.perf_counter() - start
                full_body = "".join(chunks)

                # Parse SSE events for readability
                events = parse_sse_events(full_body)

                print(f"\n  --- Stream Complete ---")
                print(f"  Total time:  {total_time*1000:.0f}ms")
                print(f"  Total chars: {len(full_body)}")
                print(f"  SSE events:  {len(events)}")

                # Print summary of event types
                event_types = {}
                for e in events:
                    t = e.get("event", "unknown")
                    event_types[t] = event_types.get(t, 0) + 1
                print(f"  Event types: {json.dumps(event_types)}")

                # Print key events
                for e in events:
                    if e.get("event") in ("message_start", "message_delta", "message_stop", "ping", "error"):
                        print(f"\n  [{e['event']}]")
                        if e.get("data"):
                            print(f"  {truncate(json.dumps(e['data'], ensure_ascii=False), 500)}")

                capture["response"] = {
                    "status": upstream.status_code,
                    "headers": resp_headers,
                    "ttfb_ms": round(latency * 1000),
                    "total_ms": round(total_time * 1000),
                    "stream_events": events,
                    "event_type_counts": event_types,
                }
                save_capture(capture, seq)

        return StreamingResponse(
            stream_and_capture(),
            status_code=upstream.status_code,
            headers={
                k: v for k, v in resp_headers.items()
                if k.lower() not in ("transfer-encoding", "content-encoding")
            },
        )
    else:
        # Non-streaming response
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0)) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=fwd_headers,
                content=body_bytes,
            )
        latency = time.perf_counter() - start
        resp_headers = dict(resp.headers)
        resp_body = resp.text

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = None

        print(f"\n  --- Response ---")
        print(f"  Status:  {resp.status_code}")
        print(f"  Latency: {latency*1000:.0f}ms")
        print(f"  --- Response Headers ---")
        print_headers(resp_headers)
        print(f"\n  --- Response Body ({len(resp_body)} chars) ---")
        if resp_json:
            formatted = json.dumps(resp_json, indent=2, ensure_ascii=False)
            print(truncate(formatted, BODY_LIMIT))
        else:
            print(truncate(resp_body, BODY_LIMIT))

        capture["response"] = {
            "status": resp.status_code,
            "headers": resp_headers,
            "latency_ms": round(latency * 1000),
            "body": resp_json or resp_body,
        }
        save_capture(capture, seq)

        return JSONResponse(
            content=resp_json if resp_json else resp_body,
            status_code=resp.status_code,
            headers={
                k: v for k, v in resp_headers.items()
                if k.lower() not in (
                    "transfer-encoding", "content-encoding",
                    "content-length",
                )
            },
        )


def parse_sse_events(raw: str) -> list[dict]:
    """Parse SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data_lines = []

    for line in raw.split("\n"):
        if line.startswith("event: "):
            if current_event or current_data_lines:
                events.append(_make_event(current_event, current_data_lines))
            current_event = line[7:].strip()
            current_data_lines = []
        elif line.startswith("data: "):
            current_data_lines.append(line[6:])
        elif line.strip() == "" and (current_event or current_data_lines):
            events.append(_make_event(current_event, current_data_lines))
            current_event = None
            current_data_lines = []

    if current_event or current_data_lines:
        events.append(_make_event(current_event, current_data_lines))

    return events


def _make_event(event_type: str | None, data_lines: list[str]) -> dict:
    raw_data = "\n".join(data_lines)
    try:
        data = json.loads(raw_data)
    except (json.JSONDecodeError, ValueError):
        data = raw_data if raw_data else None
    result = {}
    if event_type:
        result["event"] = event_type
    if data is not None:
        result["data"] = data
    return result


def main():
    global TARGET_BASE_URL, SAVE_TO_FILE, BODY_LIMIT

    parser = argparse.ArgumentParser(description="Capture proxy for Claude SDK requests")
    parser.add_argument("--target", default="https://api.anthropic.com",
                        help="Target API base URL")
    parser.add_argument("--port", type=int, default=8888, help="Local proxy port")
    parser.add_argument("--no-file", action="store_true", help="Don't save captures to files")
    parser.add_argument("--body-limit", type=int, default=2000,
                        help="Max body chars to print (0=unlimited)")
    args = parser.parse_args()

    TARGET_BASE_URL = args.target.rstrip("/")
    SAVE_TO_FILE = not args.no_file
    BODY_LIMIT = args.body_limit

    print(f"Capture Proxy starting...")
    print(f"  Listening on: http://localhost:{args.port}")
    print(f"  Forwarding to: {TARGET_BASE_URL}")
    print(f"  Save captures: {SAVE_TO_FILE}")
    if SAVE_TO_FILE:
        print(f"  Capture dir: {CAPTURE_DIR}")
    print()

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
