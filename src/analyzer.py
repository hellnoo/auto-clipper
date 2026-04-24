import json
import re
from abc import ABC, abstractmethod
from loguru import logger
from . import config

SYSTEM_PROMPT = """You are a viral short-form video producer. Given a transcript with timestamps,
pick the most viral-worthy segments for TikTok/Reels/Shorts.

Rules:
- Each clip must be between {min_sec} and {max_sec} seconds long.
- Pick between {cmin} and {cmax} clips total.
- Each clip must start and end at natural sentence boundaries where possible.
- Prioritize: strong hook in first 3 seconds, emotional peak, surprising insight, story arc, punchline.
- Write hook, caption, and hashtags in the SAME LANGUAGE as the transcript.
- Hook: <= 80 chars, attention-grabbing.
- Caption: 1-2 sentences summarizing the clip.
- Hashtags: 3-6 relevant tags (without the # symbol).
- Score: 0-100, higher = more viral potential.

Return ONLY valid JSON. No markdown fences. No prose. Exactly this shape:
{{"clips":[{{"start":<float>,"end":<float>,"hook":"...","caption":"...","hashtags":["..."],"score":<int>}}]}}"""


def _build_user_prompt(transcript: dict) -> str:
    lines = [f"Language: {transcript['language']}", f"Duration: {transcript['duration']:.1f}s", "", "Transcript:"]
    for seg in transcript["segments"]:
        lines.append(f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object found in response")
    return json.loads(m.group(0))


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str: ...


class OllamaProvider(LLMProvider):
    def __init__(self):
        import ollama
        self.client = ollama.Client(host=config.OLLAMA_HOST)
        self.model = config.OLLAMA_MODEL

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format="json",
            options={"temperature": 0.3},
        )
        return resp["message"]["content"]


class GroqProvider(LLMProvider):
    def __init__(self):
        from groq import Groq
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set")
        self.client = Groq(api_key=config.GROQ_API_KEY)
        self.model = config.GROQ_MODEL

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        return resp.choices[0].message.content


def get_provider() -> LLMProvider:
    p = config.LLM_PROVIDER
    if p == "ollama":
        return OllamaProvider()
    if p == "groq":
        return GroqProvider()
    raise ValueError(f"unknown LLM_PROVIDER: {p}")


def _validate_clips(data: dict, total_duration: float) -> list[dict]:
    clips = data.get("clips") or []
    valid: list[dict] = []
    for c in clips:
        try:
            start = float(c["start"])
            end = float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        dur = end - start
        if dur < config.CLIP_MIN_SEC - 2 or dur > config.CLIP_MAX_SEC + 5:
            continue
        if start < 0 or end > total_duration + 1:
            continue
        valid.append({
            "start": max(0.0, start),
            "end": min(total_duration, end),
            "hook": str(c.get("hook") or "").strip()[:120],
            "caption": str(c.get("caption") or "").strip(),
            "hashtags": [str(h).lstrip("#").strip() for h in (c.get("hashtags") or []) if h],
            "score": float(c.get("score") or 0),
        })
    valid.sort(key=lambda x: x["score"], reverse=True)
    return valid[: config.CLIP_COUNT_MAX]


def analyze(transcript: dict) -> list[dict]:
    provider = get_provider()
    logger.info(f"Analyzing with {config.LLM_PROVIDER} ({type(provider).__name__})")

    system = SYSTEM_PROMPT.format(
        min_sec=config.CLIP_MIN_SEC,
        max_sec=config.CLIP_MAX_SEC,
        cmin=config.CLIP_COUNT_MIN,
        cmax=config.CLIP_COUNT_MAX,
    )
    user = _build_user_prompt(transcript)

    last_err: Exception | None = None
    raw = ""
    for attempt in range(1, 4):
        try:
            if attempt == 1:
                raw = provider.complete(system, user)
            else:
                fix_user = (
                    f"Your previous response could not be parsed as JSON. Error: {last_err}\n\n"
                    f"Previous response:\n{raw[:500]}\n\n"
                    "Return ONLY valid JSON matching the schema. No markdown. No prose."
                )
                raw = provider.complete(system, fix_user)
            data = _extract_json(raw)
            clips = _validate_clips(data, transcript["duration"])
            if not clips:
                raise ValueError("no valid clips in response")
            logger.success(f"Got {len(clips)} clips from LLM")
            return clips
        except Exception as e:
            last_err = e
            logger.warning(f"LLM attempt {attempt} failed: {e}")
    raise RuntimeError(f"LLM analysis failed after 3 attempts: {last_err}")


if __name__ == "__main__":
    import sys
    with open(sys.argv[1]) as f:
        t = json.load(f)
    print(json.dumps(analyze(t), indent=2))
