from pathlib import Path
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")
load_dotenv(ROOT / ".env")  # fallback

def env(key: str, default: str = "") -> str:
    return os.getenv(key, default)

LLM_PROVIDER = env("LLM_PROVIDER", "ollama").lower()
OLLAMA_HOST = env("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = env("OLLAMA_MODEL", "qwen2.5:7b")
GROQ_API_KEY = env("GROQ_API_KEY", "")
GROQ_MODEL = env("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = env("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
OPENROUTER_REFERER = env("OPENROUTER_REFERER", "https://github.com/hellnoo/auto-clipper")
OPENROUTER_TITLE = env("OPENROUTER_TITLE", "auto-clipper")

# Multi-agent quality pipeline. When enabled, after the curator picks clips,
# a critic agent reviews each one and refines weak hooks / awkward boundaries
# before render. Adds ~1 LLM call per video (~$0.05 on Sonnet) for noticeably
# more polished output.
LLM_CRITIQUE = env("LLM_CRITIQUE", "1") == "1"

WHISPER_MODEL = env("WHISPER_MODEL", "small")
WHISPER_DEVICE = env("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = env("WHISPER_COMPUTE", "int8")

OUTPUT_DIR = ROOT / env("OUTPUT_DIR", "output")
RAW_DIR = OUTPUT_DIR / "raw"
CLIPS_DIR = OUTPUT_DIR / "clips"
FINAL_DIR = OUTPUT_DIR / "final"
for d in (RAW_DIR, CLIPS_DIR, FINAL_DIR):
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = ROOT / env("DB_PATH", "auto_clipper.db")
VIDEO_QUALITY = int(env("VIDEO_QUALITY", "720"))
CLIP_MIN_SEC = int(env("CLIP_MIN_SEC", "30"))
CLIP_MAX_SEC = int(env("CLIP_MAX_SEC", "60"))
CLIP_COUNT_MIN = int(env("CLIP_COUNT_MIN", "3"))
CLIP_COUNT_MAX = int(env("CLIP_COUNT_MAX", "7"))

# Visual polish toggles. Defaults conservative — anything that risks
# playback issues (timestamp splices, dynamic filters) defaults OFF.
# Toggle to "1" in .env to opt back in when you've validated stability.
WATERMARK_TEXT = env("WATERMARK_TEXT", "kanz × claude")  # set "" to disable
END_CARD_TEXT = env("END_CARD_TEXT", "follow biar gak ketinggalan 👀")  # set "" to disable
SILENCE_CUT = env("SILENCE_CUT", "0") == "1"             # cut dead air > 700ms (PTS-heavy)
KEN_BURNS = env("KEN_BURNS", "0") == "1"                 # slow zoom (eval=frame, can cause
                                                          # browser-stall on some Windows builds)
HOOK_BLUR_BG = env("HOOK_BLUR_BG", "0") == "1"           # blur bg during hook

# Speaker diarization (speechbrain ECAPA, no HF auth needed).
# EXPECTED_SPEAKERS:
#   0 = auto-detect via silhouette (good for 1-2 speakers)
#   N = force exactly N speakers (use this when you KNOW the host count;
#       jumps quality from ~65% to ~85% on 3+ speaker podcasts)
DIARIZE_ENABLED = env("DIARIZE_ENABLED", "0") == "1"
EXPECTED_SPEAKERS = int(env("EXPECTED_SPEAKERS", "0"))
