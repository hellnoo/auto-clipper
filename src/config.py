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

# Visual polish toggles
WATERMARK_TEXT = env("WATERMARK_TEXT", "kanz × claude")  # set blank "" to disable
KEN_BURNS = env("KEN_BURNS", "1") == "1"                 # subtle slow zoom
HOOK_BLUR_BG = env("HOOK_BLUR_BG", "1") == "1"           # blur bg during hook
