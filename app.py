import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

load_dotenv()

DEFAULT_BASE_URL = os.getenv("LITELLM_BASE_URL", "https://offeringschat.isha.in").rstrip("/")
ENV_API_KEY = os.getenv("LITELLM_API_KEY", "")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Vertex Chat (LiteLLM proxy)")

# Reject requests whose Host header is not local. Blocks DNS-rebinding pages
# (evil.com resolving to 127.0.0.1) from reaching this API via the user's browser.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


def resolve_creds(request: Request):
    """Key/base from request headers (UI Settings) override env defaults."""
    key_hdr = request.headers.get("x-api-key")
    base_hdr = request.headers.get("x-base-url")
    key = key_hdr or ENV_API_KEY
    # A client-supplied base URL is only honored together with a client-supplied
    # key: the .env key must never be sent to a caller-chosen server.
    base = (base_hdr if (base_hdr and key_hdr) else DEFAULT_BASE_URL).rstrip("/")
    return key, base


def summarize_upstream_error(status, body, content_type=""):
    """Turn ugly upstream bodies (HTML 504 pages, etc.) into a short, clear message."""
    text = body.decode("utf-8", "ignore") if isinstance(body, (bytes, bytearray)) else (body or "")
    low = text.lower()
    is_html = low.lstrip().startswith("<") or "text/html" in content_type.lower() or "<!doctype html" in low[:200]
    if status in (502, 503, 504) or "gateway time-out" in low or "gateway timeout" in low:
        return (f"Upstream {status}: proxy/model gateway timed out (offeringschat.isha.in). "
                "Usually transient — retry. If it persists the model is slow/cold or the proxy is overloaded; "
                "large images make it worse.")
    if is_html:
        return f"Upstream {status}: proxy returned an HTML error page (gateway down or blocking), not an API response."
    return f"Upstream {status}: {text[:400]}"


@app.get("/api/health")
async def health():
    return {"ok": True, "base_url": DEFAULT_BASE_URL, "has_env_key": bool(ENV_API_KEY)}


@app.post("/api/feedback")
async def feedback(request: Request):
    """Message + optional screenshots → GitHub issue on the shared private
    feedback inbox (see app_feedback.py). Never proxies through the LLM
    gateway — this goes straight to GitHub with the local token."""
    import base64
    import tempfile

    import app_feedback
    from fastapi.concurrency import run_in_threadpool

    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Empty message.")
    kind = body.get("kind") or "Feedback"
    sender = (body.get("name") or "").strip()

    files = []
    tmpdir = tempfile.mkdtemp(prefix="vertex_chat_feedback_")
    for att in (body.get("attachments") or [])[:5]:
        name = os.path.basename(att.get("name") or "screenshot.png")
        try:
            data = base64.b64decode(att.get("data") or "")
        except (ValueError, TypeError):
            continue
        if not data or len(data) > app_feedback.MAX_ATTACHMENT_MB * 1024 * 1024:
            continue
        p = os.path.join(tmpdir, name)
        with open(p, "wb") as f:
            f.write(data)
        files.append(p)

    try:
        url = await run_in_threadpool(
            app_feedback.send_feedback, "Vertex Chat", "", kind, sender,
            message, files)
        return {"ok": True, "url": url}
    except Exception as exc:  # noqa: BLE001 — network/token/API, report cleanly
        try:
            saved = app_feedback.save_locally("Vertex Chat", "", kind, sender,
                                              message, files)
        except OSError:
            saved = ""
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": str(exc),
                                     "saved": saved})


@app.get("/api/models")
async def models(request: Request):
    key, base = resolve_creds(request)
    if not key:
        raise HTTPException(status_code=400, detail="No API key. Set it in Settings or LITELLM_API_KEY.")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(f"{base}/v1/models", headers={"Authorization": f"Bearer {key}"})
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Cannot reach {base}: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code,
                            detail=summarize_upstream_error(r.status_code, r.text, r.headers.get("content-type", "")))
    data = r.json()
    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
    return {"models": sorted(ids)}


@app.post("/api/chat")
async def chat(request: Request):
    key, base = resolve_creds(request)
    if not key:
        raise HTTPException(status_code=400, detail="No API key. Set it in Settings or LITELLM_API_KEY.")

    body = await request.json()
    model = body.get("model")
    if not model:
        raise HTTPException(status_code=400, detail="No model selected.")

    messages = body.get("messages", [])
    system = (body.get("system") or "").strip()
    use_cache = body.get("cache", True)
    stream = bool(body.get("stream", True))

    final_messages = []
    if system:
        if use_cache:
            # Stable, marked-cacheable system prefix lets the backend reuse it (prompt caching).
            final_messages.append({
                "role": "system",
                "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            })
        else:
            final_messages.append({"role": "system", "content": system})
    final_messages.extend(messages)

    payload = {"model": model, "messages": final_messages, "stream": stream}
    for opt in ("temperature", "top_p", "max_tokens"):
        if body.get(opt) is not None:
            payload[opt] = body[opt]
    if stream:
        payload["stream_options"] = {"include_usage": True}

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = f"{base}/v1/chat/completions"

    if not stream:
        # Long timeout: audio/video attachments make the model slow to first byte.
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(url, headers=headers, json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code,
                                detail=summarize_upstream_error(r.status_code, r.text, r.headers.get("content-type", "")))
        data = r.json()
        cost = r.headers.get("x-litellm-response-cost")
        if cost is not None:
            data["_litellm_cost"] = cost  # USD, computed by the LiteLLM proxy
        return JSONResponse(data)

    async def event_stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as r:
                if r.status_code != 200:
                    err = await r.aread()
                    msg = summarize_upstream_error(r.status_code, err, r.headers.get("content-type", ""))
                    yield f"data: {json.dumps({'error': msg})}\n\n".encode()
                    return
                try:
                    async for chunk in r.aiter_raw():
                        if chunk:
                            yield chunk
                except httpx.HTTPError as e:
                    yield f"data: {json.dumps({'error': f'Stream interrupted by proxy/gateway: {e}'})}\n\n".encode()
                    return
                # Emit the proxy-computed cost as a final SSE frame (header available once response opened).
                cost = r.headers.get("x-litellm-response-cost")
                if cost is not None:
                    yield f"data: {json.dumps({'litellm_cost': cost})}\n\n".encode()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
