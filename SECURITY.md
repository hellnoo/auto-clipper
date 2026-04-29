# Security & Privacy

Quick reference for what's safe to commit and what's not.

## Files NEVER committed (gitignored)

These contain secrets — if you find any of them in `git ls-files`, treat it as a leak:

| File | What it holds | Damage if leaked |
|---|---|---|
| `config/.env` | Groq, OpenRouter, HF tokens | Attacker burns your API quota / steals models |
| `config/youtube_client.json` | Google OAuth client ID + secret | Used together with token below |
| `config/youtube_token.json` | YouTube refresh token | **Attacker uploads to your channel** |
| `config/instagram_session.json` | IG login session cookies (future) | Account takeover |
| `config/tiktok_cookies.json` | TikTok auth cookies (future) | Account takeover |
| `*cookies.txt` | yt-dlp browser cookies | YouTube account access |
| `auto_clipper.db` | Watermark text, video URLs | Mostly low risk, but private content trail |

## Files safe to commit

- `config/.env.example` — placeholder values only
- All `src/`, `dashboard/`, `run.ps1`, `run.bat` — code
- `requirements.txt`, `Dockerfile`, `Makefile` — build config
- `README.md`, `ROADMAP.md`, `SECURITY.md` — docs

## If you leak a secret

| Service | Revoke at |
|---|---|
| Groq | https://console.groq.com/keys → 🗑 |
| OpenRouter | https://openrouter.ai/keys → 🗑 |
| HuggingFace | https://huggingface.co/settings/tokens → 🗑 |
| YouTube OAuth | https://console.cloud.google.com → APIs → Credentials → delete client |
| YouTube token | Delete `config/youtube_token.json` and reconnect (Google rotates) |

After revoking: generate a fresh credential and update your local `.env` only.

## Audit before pushing

Before `git push`, especially if you've been sharing the repo:

```bash
# Check no secrets in tracked files
git ls-files | xargs grep -lE "(sk-or-v1-[a-z0-9]{10,}|gsk_[a-zA-Z0-9]{20,}|hf_[a-zA-Z0-9]{20,})"

# Check git history hasn't accidentally committed secrets
git log --all -p | grep -E "(sk-or-v1-[a-z0-9]{30}|gsk_[a-zA-Z0-9]{30,}|hf_[a-zA-Z0-9]{30,})" | head
```

Both should return nothing.

## Threat model

This project is designed for **personal / single-user** use. Treat the local
`config/` folder as you would your `.ssh/` directory — never share, never
commit.

- The dashboard binds to `127.0.0.1` only by default — not exposed to network.
- API keys / OAuth tokens stay on the local filesystem under `config/`.
- DB only tracks video URLs and clip metadata; no passwords stored.
- YouTube uploads use Google's official OAuth — your password never touches
  this app.

If you deploy this to a shared server, harden:
- Set `OUTPUT_DIR` outside the web root
- Add HTTP auth in front of the dashboard (nginx basic auth, etc.)
- Use environment variables instead of `config/.env` for credentials
- Encrypt the SQLite DB at rest
