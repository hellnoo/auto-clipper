# Roadmap

## ✅ Done (MVP + v1.1)

- yt-dlp + 5-client fallback chain for YouTube
- Cobalt fallback when datacenter IPs are blocked at SSL layer
- faster-whisper transcription with word-level timestamps
- GPU auto-detect (CUDA float16, falls back to CPU int8)
- Transcript cache keyed on `<video>.transcript.<model>.json`
- Existing-mp4 short-circuit for YouTube re-submits
- LLM provider abstraction: Ollama (local), Groq (free), OpenRouter (Claude/GPT-4/Gemini)
- Adaptive transcript condensing to fit Groq's 12k TPM cap
- 10 viral hook templates baked into the system prompt
- Time-spread enforcement (≥ 90s between clip starts)
- English hook + caption defaults regardless of source language
- Per-word boundary snapping so cuts never land mid-syllable
- Smart silence cut (gaps > 450ms with audio + caption re-sync)
- Face-aware 9:16 crop (OpenCV Haar cascade, median centroid, clamped 0.20–0.80)
- Hook overlay (Impact 108pt, fade + bounce, 2.5s)
- Per-word caption highlight (Montserrat 82pt, active word 92pt yellow bold)
- Emoji pop-ups tagged by the LLM, rendered with bounce animation
- FastAPI dashboard with submit form, queue, auto-refresh
- Per-clip download button + file size + emoji chips
- Regenerate-clips button (re-run analyze + render on cached transcript)
- HTML escaping on all user/LLM-supplied content (XSS hardening)
- SQLite tracking (videos + clips + emojis with idempotent migrations)
- Cost tracking — per-call token usage + USD estimate for Groq + OpenRouter
- One-click Windows launcher (`run.bat` / `run.ps1`) with auto deps,
  auto NVIDIA detection + cuDNN/cuBLAS install, animated CRT-scan banner
- HF Spaces Docker deploy + GitHub Actions sync (currently flagged abusive
  by HF; local-first is the supported path)

## 🟡 V1.2 — Quality polish (next 1-2 weeks)

- **Hook A/B generation** — produce 3 hook variants per clip, second LLM
  call picks the strongest; or surface in dashboard for user choice
- **Better silence detection** — supplement word timestamps with
  audio-energy VAD so we catch instrumental gaps the transcript doesn't
- **Speaker-aware face tracking** — if multiple faces detected, pick the
  one whose mouth-open delta correlates with audio amplitude (active speaker)
- **B-roll cue insertion** — extend emoji system to also drop suggested
  stock-footage / GIF placeholders the user can swap in later
- **Thumbnail extraction** — pick the highest-confidence face frame per
  clip as `poster.jpg`
- **ZIP bundle download** — one button → zip of mp4 + caption + thumbnail
  for every clip in a video

## 🟠 V1.5 — UX / scale

- **In-browser clip editor** — drag handles to retrim start/end, regen
  hook, edit caption, swap emoji, re-render
- **Bulk-URL upload UI** — drag-drop / paste many URLs at once
- **Multiple aspect-ratio variants** — 9:16, 1:1, 16:9 from one render
- **Watermark overlay** — burn user-configurable handle into a corner
- **Caption position toggle** — top / middle / bottom per clip
- **Public deploy that doesn't get flagged** — Cloudflare Workers proxy
  in front of yt-dlp + cobalt, or self-hosted VPS with rotating IPs
- **Concurrent worker pool** — render multiple clips in parallel when CPU/GPU allows

## 🔵 V2 — Automation

- **Auto-upload** to TikTok / Reels / Shorts via official APIs (or
  browser automation where APIs are restricted)
- **Schedule + queue posting** — drip across the day for algorithm
- **A/B testing** — push two hook variants to different audiences,
  pick winner by 24h retention metric
- **RSS / playlist watcher** — auto-clip every new episode of a podcast
  or YouTube channel
- **Per-clip analytics** — pull views / retention / saves back from each
  platform; train a ranking model on your own performance data

## 🟣 V3 — Productize (if it justifies)

- SaaS tier (free 5 clips/month, paid unlimited)
- Team workspaces with shared library + brand profile (logo, font, colors)
- Public REST API for developers
- White-label for agencies
- Browser extension for one-click clip-from-current-tab

## Known issues / debt

- **No tests.** Pure dev velocity to date.
- **Single-threaded worker.** N submitted URLs serialize.
- **OpenRouter `response_format=json_object`** isn't supported by every
  model on the catalogue; defaults assume Claude / GPT-4o / Gemini 2.5+.
- **HF Space flagged abusive** by the auto-handler ("Cloudflare" rule).
  Local-first is the supported deploy path. A clean public deploy
  needs work in V1.5.
- **HTML escaping is post-hoc.** A jinja2 template would be more robust
  than f-string concatenation in `dashboard/app.py`.
