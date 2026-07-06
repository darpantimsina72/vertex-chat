# Vertex Chat

A simple local chat app for talking to Vertex AI models through a LiteLLM proxy
(OpenAI-compatible API). Built for translation and content teams — attach images,
audio, video or documents, keep multi-turn memory, and save cost with prompt caching.

Runs entirely on your own computer. Your API key never leaves your machine except
to call the proxy.

## Features

- **Chat with any model** exposed by your LiteLLM proxy (model picker in the top bar)
- **Attachments** — click 📎, paste, or drag & drop:
  - **Images** (jpg/png/…) — auto-downscaled to 1024px to save tokens
  - **Audio** (mp3/wav/…) — sent as audio input for transcription/analysis
  - **Video** (mp4/…) — sent inline (25 MB limit)
  - **Documents** — PDFs sent as files; text files (`.txt .md .csv .json .srt .vtt` …) inlined into the message
- **Prompt caching** — toggle in the sidebar (on by default). The system prompt is
  marked cacheable so repeated turns reuse it instead of re-billing it
- **Streaming** responses with live token + cost display (per turn and per session)
- **Multi-turn memory** — the whole conversation is sent each turn; history survives page reloads
- **System instructions, temperature, top-p, max tokens** — all adjustable in the sidebar

## Quick start

### Windows

1. Install Python 3.12 or 3.13 from [python.org/downloads](https://www.python.org/downloads/)
   — **tick "Add python.exe to PATH"** during install.
2. Download this project (green **Code** button → *Download ZIP* → unzip), or `git clone` it.
3. Double-click **`run.bat`**.

That's it. The first run installs everything automatically (needs internet once),
then your browser opens at `http://127.0.0.1:8000`. Later launches work offline
and start instantly. Keep the black window open while using the app.

### macOS / Linux

```bash
git clone <this-repo-url>
cd vertex-chat
./run.sh
```

(Or `python3 start.py` — same thing.)

### Add your API key

Two options:

- In the app: click **⚙ Settings** (top right), paste your `sk-…` key, Save.
  Stored only in your browser.
- Or edit the `.env` file created next to the app and set `LITELLM_API_KEY=sk-…`.

Then pick a model in the top bar and chat.

## Configuration

`.env` (created automatically from `.env.example` on first run):

| Variable | Meaning | Default |
|---|---|---|
| `LITELLM_BASE_URL` | Your LiteLLM proxy URL | `https://offeringschat.isha.in` |
| `LITELLM_API_KEY` | Your API key (`sk-…`) | empty — set in app ⚙ instead |
| `PORT` | Local port for the app | `8000` |

Both URL and key can also be set per-browser in the app's ⚙ Settings, which
overrides `.env`.

## Notes & limits

- **Attachment size**: audio/video/PDF capped at **25 MB** — bigger files inflate 33%
  as base64 and the gateway times out. Compress or trim long media first.
- **Audio/video support depends on the model** — pick a multimodal model (e.g. a
  Gemini model). If the model rejects the attachment, the error shows in the chat.
- **504 / gateway timeout**: usually transient — just retry. Large attachments make
  it more likely.
- **Prompt caching**: works when the system prompt stays identical between turns.
  Change the system prompt and the cache resets. Cached token counts show in the
  usage line under the composer.
- Your chat history is stored in your browser's localStorage only, never on a server.

## Project layout

```
app.py            FastAPI backend — proxies chat/model calls to LiteLLM
static/           Web UI (plain HTML/JS/CSS, no build step)
start.py          Cross-platform launcher (venv + deps + server + browser)
run.bat           Windows double-click launcher
run.sh            macOS/Linux launcher
requirements.txt  Python dependencies
.env.example      Config template (copy to .env)
```

## Troubleshooting the install (Windows)

The installer retries automatically and explains failures in plain language.
If it still fails:

| Symptom | Fix |
|---|---|
| `ConnectionResetError (10054)` / "Connection broken" during install | VPN, firewall or antivirus is cutting the download. Re-run (it resumes), try once without VPN, or use a phone hotspot just for the one-time install. |
| `CERTIFICATE_VERIFY_FAILED` / SSL errors | Corporate network intercepts SSL. The installer offers a relaxed-SSL retry — answer `y`. |
| "It looks like you are running this from inside the ZIP" | Right-click the downloaded ZIP → **Extract All…**, then run `run.bat` from the extracted folder. |
| "Python is not installed" but you installed it | You got the Microsoft Store stub. Install from [python.org](https://www.python.org/downloads/) and tick **Add python.exe to PATH**. |
| `Access is denied` / `WinError 5` | Move the folder out of OneDrive/Downloads to e.g. `C:\vertex-chat`, or add it to your antivirus exclusions. |
| Double-clicked `run.bat` but the app is already open | Fine — it just re-opens the existing app in your browser. It never starts a duplicate. |
| Browser doesn't open by itself | Open the address printed in the black window (usually `http://127.0.0.1:8000`). |
| Broken half-install | Just re-run `run.bat` — it repairs itself. Worst case: delete the `.venv` folder and re-run. |

The install needs internet **once**. After that the app starts offline
(only chatting needs to reach the proxy).

## Security

- Never commit `.env` — it's in `.gitignore`. Only `.env.example` (no key) goes to GitHub.
- The server binds to `127.0.0.1` only — not reachable from other machines. It also
  rejects requests with a non-local Host header (blocks DNS-rebinding tricks from
  malicious websites), and never sends the `.env` key to any server other than the
  configured proxy.
- The optional relaxed-SSL install retry skips certificate checks for the Python
  package servers (pypi.org, files.pythonhosted.org) for that single command only,
  and only after you explicitly agree. Use it on trusted office networks, not
  public Wi-Fi.
