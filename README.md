---
title: Auto Clipper
emoji: 🎬
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# auto-clipper

Automate short-form (TikTok/Reels/Shorts) clips from long videos — 100% free, no paid API required.

Pipeline: **yt-dlp** → **faster-whisper** (local) → **Ollama or Groq** (local/free) → **ffmpeg** (cut + 9:16 crop + burned word-by-word captions).

## Requirements

- Python 3.11+
- `ffmpeg` on your PATH — https://ffmpeg.org/download.html
- One LLM backend:
  - **Ollama** (default, free forever, runs locally) — https://ollama.com
  - **Groq** (free tier, cloud, fast) — https://console.groq.com

### Install ffmpeg

- **Windows**: download a build from https://www.gyan.dev/ffmpeg/builds/ and add the `bin` folder to `PATH`, or `winget install Gyan.FFmpeg`.
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg` (Debian/Ubuntu) or distro equivalent.

## Quick start

```bash
git clone <repo> auto-clipper
cd auto-clipper
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
cp config/.env.example config/.env
```

### Option A: Ollama (recommended, free, local)

Install Ollama:

- **Linux/macOS**: `curl -fsSL https://ollama.com/install.sh | sh`
- **Windows**: download installer from https://ollama.com/download

Pull a model:

```bash
ollama pull qwen2.5:7b
# quick sanity check:
ollama run qwen2.5:7b "say hi"
```

If your machine can't run 7B comfortably, try a smaller one and set it in `.env`:

```bash
ollama pull qwen2.5:3b
# then in config/.env:
# OLLAMA_MODEL=qwen2.5:3b
```

### Option B: Groq (free tier, no GPU needed)

1. Get a free API key at https://console.groq.com (no credit card).
2. Edit `config/.env`:

```
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_xxx
```

## Run

Single URL:

```bash
python -m src.main --url "https://www.youtube.com/watch?v=..."
```

Batch — put one URL per line in `sources.txt`:

```bash
python -m src.main --batch sources.txt
```

Dashboard (browse and preview generated clips):

```bash
python -m uvicorn dashboard.app:app --reload --port 8000
# open http://localhost:8000
```

With `make`:

```bash
make install
make run URL="https://..."
make batch
make dashboard
make ollama-setup
```

## Output

- `output/raw/` — downloaded source videos
- `output/final/` — rendered 9:16 clips (`*.mp4`) + caption files (`*.txt`)
- `auto_clipper.db` — SQLite tracking DB
- `output/auto_clipper.log` — rotating log

## Configuration (`config/.env`)

| Key | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` or `groq` |
| `OLLAMA_HOST` | `http://localhost:11434` | |
| `OLLAMA_MODEL` | `qwen2.5:7b` | try `llama3.1:8b`, `qwen2.5:3b` |
| `GROQ_API_KEY` | _(empty)_ | required if `LLM_PROVIDER=groq` |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | |
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium` |
| `WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` |
| `WHISPER_COMPUTE` | `int8` | `int8` on CPU, `float16` on GPU |
| `VIDEO_QUALITY` | `720` | max download height |
| `CLIP_MIN_SEC` / `CLIP_MAX_SEC` | `30` / `60` | per-clip duration bounds |
| `CLIP_COUNT_MIN` / `CLIP_COUNT_MAX` | `3` / `7` | how many clips to pick |
| `COBALT_API_URL` | `https://api.cobalt.tools` | YouTube fallback when yt-dlp is blocked (datacenter IPs). Override with a working public/self-hosted [cobalt](https://github.com/imputnet/cobalt) instance. |
| `COBALT_API_KEY` | _(empty)_ | optional API key if your cobalt instance requires auth. |

## Testing individual modules

Each module runs standalone:

```bash
python -m src.db                         # init DB
python -m src.downloader <url>           # download only
python -m src.transcriber output/raw/x.mp4   # transcribe only
python -m src.editor                     # check ffmpeg on PATH
```

## Troubleshooting

- **`ffmpeg not found`** — install ffmpeg and ensure it's on your PATH (restart terminal after install).
- **Ollama connection refused** — Ollama isn't running. Start it: `ollama serve` (on Windows it runs in the tray after install).
- **Out of memory with 7B model** — switch to `qwen2.5:3b` or use Groq.
- **Garbage/empty clips** — bump `WHISPER_MODEL` to `medium` (better transcription = better analysis).

## Deploy to Hugging Face Spaces (free, public URL)

The repo is HF-Spaces-ready (Docker SDK, port 7860). On the free tier you get **2 vCPU / 16GB RAM**, no GPU, ephemeral filesystem — enough for Whisper `small` + ffmpeg, **but not for Ollama 7B**. Force Groq mode.

1. Create a new Space at https://huggingface.co/new-space
   - **SDK**: Docker
   - **Hardware**: CPU basic (free)
2. Push this repo to the Space's git remote (or upload via the web UI).
3. In the Space's **Settings → Variables and secrets**, add:
   - `LLM_PROVIDER` = `groq`
   - `GROQ_API_KEY` = `gsk_...` (from https://console.groq.com)
   - Optional: `GROQ_MODEL`, `WHISPER_MODEL` (use `base` for faster startup)
4. Space auto-builds. First boot ~5 min (downloads Whisper model). Open the Space URL → submit a YouTube link.

**Free-tier caveats:**
- One job at a time (queue is in-memory).
- Filesystem is ephemeral — clips disappear on restart unless you enable persistent storage (paid).
- Long videos (>20 min) are slow on 2 vCPU. Use `WHISPER_MODEL=base` if too slow.
- `OLLAMA_*` vars are ignored when `LLM_PROVIDER=groq`.

## Self-hosting cobalt for YouTube (free, ~10 min)

YouTube blocks the public `api.cobalt.tools` (JWT-only) and HF Spaces' datacenter IP at the SSL layer. The fix is to run your own [cobalt](https://github.com/imputnet/cobalt) instance — it uses better fingerprinting than plain yt-dlp and usually gets through.

**Deploy on Koyeb (free, 1 nano, no sleep):**

1. Sign up at https://app.koyeb.com (GitHub login).
2. Create app → **Deploy a Docker image**.
   - Image: `ghcr.io/imputnet/cobalt:10`
   - Port: `9000` (HTTP)
   - Region: pick the closest one.
3. Add **Environment variables**:
   - `API_URL` = `https://<your-app>.koyeb.app/` — you get the URL after first deploy; redeploy after setting this.
   - `API_PORT` = `9000`
   - `CORS_WILDCARD` = `1`
   - (Optional, recommended) `API_KEY_URL` = `https://gist.githubusercontent.com/.../keys.json` with a JSON like `{"yourkey":{"name":"main","limit":1000}}`, then set `API_AUTH_REQUIRED=1`. Skip for first test.
4. Deploy. Wait ~1 min. Hit `https://<your-app>.koyeb.app/api/serverInfo` → should return JSON.
5. In your HF Space → **Settings → Variables and secrets**:
   - `COBALT_API_URL` = `https://<your-app>.koyeb.app`
   - `COBALT_API_KEY` = `yourkey` (only if you enabled auth above)
6. Restart the Space. Submit a YT URL → log should show `trying cobalt fallback at https://<your-app>.koyeb.app` → success.

**If YT still blocks your Koyeb IP:** add a YouTube proxy to cobalt — set `YOUTUBE_PROXY=http://user:pass@host:port` in cobalt env. Free residential trials: Webshare (10 IPs free), IPRoyal ($1.75 trial). Otherwise downloads on the Koyeb IP work for most videos but may fail on viral / age-gated ones.

**Alternative hosts:** Render (free, sleeps after 15min — first request slow), Northflank (free hobby), Fly.io ($5 credit). Same image + env vars.

## Local Docker (optional)

```bash
docker build -t auto-clipper .
docker run --rm -p 7860:7860 \
  -e LLM_PROVIDER=groq -e GROQ_API_KEY=gsk_... \
  -v $(pwd)/output:/data/output \
  auto-clipper
# open http://localhost:7860
```

## Web dashboard

Whether running locally (`make dashboard`) or on HF Spaces, the dashboard at `/` has a URL submit form. Jobs run in a background worker, one at a time. The page auto-refreshes every 15s so you can watch status (`pending → downloading → transcribing → analyzing → rendering → done`).

## Roadmap

MVP ships without auto-upload — clips land in `output/final/` (or `/data/output` in container), manual upload from there. Uploaders (TikTok/YouTube/Instagram) are a future addition.
