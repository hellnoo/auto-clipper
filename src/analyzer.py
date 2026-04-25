import json
import re
from abc import ABC, abstractmethod
from loguru import logger
from . import config

SYSTEM_PROMPT = """You are a top-tier short-form video producer who has shipped clips with millions of views on TikTok/Reels/Shorts. Given a long-form transcript with timestamps, you extract the segments most likely to go viral.

Hard constraints:
- Each clip MUST be between {min_sec} and {max_sec} seconds. Do NOT exceed {max_sec}s.
- Pick between {cmin} and {cmax} clips total. Quality > quantity.
- start/end MUST be inside the transcript timeline. Snap to the start of the first sentence and end of the last sentence in the segment — never cut mid-sentence.
- Clips MUST NOT overlap each other.

What makes a segment viral (rank candidates by these):
1. Hook in the first 3 seconds: a bold claim, a question, a contrarian take, a "you won't believe", a number, or a cliffhanger.
2. Self-contained payoff: the viewer gets a complete idea, story, or punchline without needing the rest of the video.
3. High emotional density: surprise, anger, awe, humor, or a relatable pain point.
4. Concrete > abstract: specific numbers, names, examples beat vague advice.
5. Visual or verbal pattern interrupt: shifts in tone, a controversial statement, a reveal.

Avoid: rambling intros, "um/uh" filler stretches, generic advice, repeated content, anything that requires earlier context to understand.

Output fields:
- hook: <= 80 chars, written as the on-screen text overlay for the first 2.5s. Punchy, no period at end. Same language as transcript.
- caption: 1-2 sentences for the post description. Same language as transcript.
- hashtags: 3-6 relevant tags (lowercase, no # prefix, no spaces).
- score: 0-100. Reserve 80+ for genuinely strong clips. Be honest — a 60 is fine.

Return ONLY a JSON object. No markdown fences. No prose. Exactly this shape:
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


def _validate_clips(data: dict, total_duration: float) -> tuple[list[dict], list[str]]:
    clips = data.get("clips") or []
    valid: list[dict] = []
    rejected: list[str] = []
    # Hard floor/ceiling — clamp to source bounds and a sane absolute range
    abs_min = max(5.0, config.CLIP_MIN_SEC * 0.5)
    abs_max = min(total_duration, config.CLIP_MAX_SEC * 1.5)
    for c in clips:
        try:
            start = float(c["start"])
            end = float(c["end"])
        except (KeyError, TypeError, ValueError):
            rejected.append(f"bad-fields:{c!r:.80}")
            continue
        start = max(0.0, start)
        end = min(total_duration, end)
        if end - start <= 0:
            rejected.append(f"end<=start:{start:.1f}-{end:.1f}")
            continue
        dur = end - start
        if dur < abs_min or dur > abs_max:
            rejected.append(f"bad-dur:{dur:.1f}s (allowed {abs_min:.0f}-{abs_max:.0f})")
            continue
        valid.append({
            "start": start,
            "end": end,
            "hook": str(c.get("hook") or "").strip()[:120],
            "caption": str(c.get("caption") or "").strip(),
            "hashtags": [str(h).lstrip("#").strip() for h in (c.get("hashtags") or []) if h],
            "score": float(c.get("score") or 0),
        })
    valid.sort(key=lambda x: x["score"], reverse=True)
    return valid[: config.CLIP_COUNT_MAX], rejected


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
    last_rejected: list[str] = []
    raw = ""
    for attempt in range(1, 4):
        try:
            if attempt == 1:
                raw = provider.complete(system, user)
            else:
                if last_rejected:
                    fix_user = (
                        f"Your previous response had {len(last_rejected)} clip(s) but none passed validation.\n"
                        f"Reasons: {last_rejected[:3]}\n\n"
                        f"Constraints reminder:\n"
                        f"- Video duration: {transcript['duration']:.1f}s\n"
                        f"- Each clip MUST be between {config.CLIP_MIN_SEC} and {config.CLIP_MAX_SEC} seconds long.\n"
                        f"- 'start' and 'end' must be within [0, {transcript['duration']:.1f}] (in seconds).\n\n"
                        f"Original request:\n{user}"
                    )
                else:
                    fix_user = (
                        f"Your previous response could not be parsed as JSON. Error: {last_err}\n\n"
                        f"Previous response:\n{raw[:400]}\n\n"
                        "Return ONLY valid JSON matching the schema. No markdown. No prose."
                    )
                raw = provider.complete(system, fix_user)
            data = _extract_json(raw)
            clips, rejected = _validate_clips(data, transcript["duration"])
            last_rejected = rejected
            if not clips:
                logger.warning(f"raw response: {raw[:600]}")
                raise ValueError(f"no valid clips ({len(rejected)} rejected: {rejected[:3]})")
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
