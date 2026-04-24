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

## Roadmap

MVP ships without auto-upload — clips land in `output/final/`, manual upload from there. Uploaders (TikTok/YouTube/Instagram) are a future addition.
