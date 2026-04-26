import json
import re
from abc import ABC, abstractmethod
from loguru import logger
from . import config

SYSTEM_PROMPT = """You are a top-tier short-form video editor who has shipped clips with millions of views on TikTok / Reels / Shorts. You think in viral patterns, not summaries. Given a long-form transcript with timestamps, you extract the segments most likely to explode.

# Hard constraints
- Each clip MUST be between {min_sec} and {max_sec} seconds. Never exceed {max_sec}s.
- Pick between {cmin} and {cmax} clips. Quality > quantity.
- start/end MUST be inside the transcript timeline.
- Snap start to the beginning of a sentence and end to the end of a sentence. Never cut mid-thought.
- Clips MUST NOT overlap.
- **Time-spread:** distribute picks across the video. No two clips may start within 90 seconds of each other. Hunt the whole timeline, not just the intro.

# Hook engineering (THE most important part)
The hook is the first 3 seconds — if it doesn't stop the scroll, nothing else matters. Use one of these proven templates as a starting structure:

1. **Bold claim** — "Most people get [X] completely wrong"
2. **Counterintuitive** — "Stop doing [X]. Here's why."
3. **Question** — "Why does [X] always [Y]?"
4. **Number list** — "3 things nobody tells you about [X]"
5. **POV** — "POV: you just realized [X]"
6. **Storytime cliffhanger** — "I almost [X] until I learned this"
7. **Confession** — "Nobody talks about this but..."
8. **Stat shock** — "97% of people [X]. Are you one of them?"
9. **Mini-reveal** — "The real reason [X] is [unexpected Y]"
10. **Direct address** — "If you're [audience], watch this"

Pick the template that best matches the segment's actual content. Make it punchy, present-tense, no clickbait that the clip doesn't pay off.

# Picking the segment
Rank candidates by:
1. **Hook potential** — does the first sentence already contain a claim, question, or pattern interrupt?
2. **Self-contained payoff** — viewer gets a complete idea/story/joke without needing earlier context.
3. **Emotional density** — surprise, anger, awe, humor, relatable frustration.
4. **Concrete > abstract** — specific numbers, names, examples beat vague advice.
5. **Quotable line** — is there a sentence people would screenshot or repeat?

Avoid: rambling intros, filler ("um", "so basically"), generic motivation, anything requiring the previous 10 minutes to understand.

# Output language
- The clip's audio is in the transcript's original language.
- The on-screen **hook** and the **caption** MUST be written in **English** (for global TikTok / Reels / Shorts reach), even if the transcript is in another language. Translate the meaning, don't transcribe the sound.
- Hashtags: English, lowercase, no spaces, no `#` prefix.

# Output fields
- `hook`: ≤ 70 chars. On-screen text. No period at end. Punchy. English.
- `caption`: 1-2 sentences for post description. Hook the reader, then tease the payoff. End with a CTA or question. English.
- `hashtags`: 3-6 relevant tags (lowercase, no #).
- `score`: 0-100. 80+ = genuinely strong, 60-79 = solid, below 60 = don't bother.
- `emojis`: 4-8 entries. Each is `{{"word":"<single word from the clip transcript, in the original language>","emoji":"<single emoji>"}}`. Pick words that hit emotionally — money, surprise, anger, success, fail, secret, mind-blown, fire, time, etc. The emoji will pop above the caption when that word is spoken. Skip filler words. Same word may appear once.

Return ONLY a JSON object. No markdown fences. No prose. Exactly this shape:
{{"clips":[{{"start":<float>,"end":<float>,"hook":"...","caption":"...","hashtags":["..."],"score":<int>,"emojis":[{{"word":"...","emoji":"..."}}]}}]}}"""


def _condense_segments(segments: list[dict], target_chunk_sec: float = 20.0) -> list[dict]:
    """Group short whisper segments into ~target_chunk_sec chunks to shrink the prompt
    for very long videos. Keeps the LLM from drowning in 1000+ tiny lines."""
    if not segments:
        return []
    out: list[dict] = []
    cur = {"start": segments[0]["start"], "end": segments[0]["end"], "text": segments[0]["text"]}
    for seg in segments[1:]:
        if seg["end"] - cur["start"] < target_chunk_sec:
            cur["end"] = seg["end"]
            cur["text"] = (cur["text"] + " " + seg["text"]).strip()
        else:
            out.append(cur)
            cur = {"start": seg["start"], "end": seg["end"], "text": seg["text"]}
    out.append(cur)
    return out


def _build_user_prompt(transcript: dict, target_input_tokens: int = 7000) -> str:
    """Adaptively condense the transcript so the prompt fits the LLM's TPM budget.

    Groq free tier caps at 12000 TPM for llama-3.3-70b. We aim for ~7k input
    tokens, leaving ~4k for response + system prompt. Roughly 4 chars per token,
    so target ~28000 chars of transcript.
    """
    segs = transcript["segments"]
    duration = transcript.get("duration") or (segs[-1]["end"] if segs else 0)

    def render(segs_list):
        return "\n".join(
            f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segs_list
        )

    target_chars = target_input_tokens * 4
    body = render(segs)
    # Try increasingly coarse condensing until we fit.
    for chunk_sec in (0, 15, 25, 40, 60, 90, 120):
        if len(body) <= target_chars:
            break
        if chunk_sec == 0:
            continue  # original was already too big
        condensed = _condense_segments(transcript["segments"], target_chunk_sec=float(chunk_sec))
        body = render(condensed)
        segs = condensed
    if len(body) > target_chars:
        # Last resort: hard truncate (shouldn't happen for any sane video length).
        body = body[:target_chars] + "\n[... transcript truncated to fit budget ...]"

    header = [
        f"Language: {transcript['language']}",
        f"Duration: {duration:.1f}s",
        f"Segments shown: {len(segs)}",
        "",
        "Transcript:",
    ]
    return "\n".join(header) + "\n" + body


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object found in response")
    return json.loads(m.group(0))


# Per-1M-token pricing (USD) for cost estimation. Approximate, used only for logging.
# Source: provider docs as of 2026-04. If your model isn't here, cost just shows "?".
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenRouter / Anthropic
    "anthropic/claude-opus-4.7":     (15.0, 75.0),
    "anthropic/claude-sonnet-4.5":   (3.0, 15.0),
    "anthropic/claude-sonnet-4.6":   (3.0, 15.0),
    "anthropic/claude-haiku-4.5":    (0.80, 4.0),
    "anthropic/claude-3.5-sonnet":   (3.0, 15.0),
    # OpenRouter / OpenAI
    "openai/gpt-4o":                 (2.5, 10.0),
    "openai/gpt-4o-mini":            (0.15, 0.60),
    "openai/o1-mini":                (3.0, 12.0),
    # OpenRouter / Google
    "google/gemini-2.5-flash":       (0.075, 0.30),
    "google/gemini-2.5-pro":         (1.25, 5.0),
    # Groq (free tier — cost is $0 but we still log token counts)
    "llama-3.3-70b-versatile":       (0.0, 0.0),
    "llama-3.1-8b-instant":          (0.0, 0.0),
}


def _estimate_cost(model: str, in_tok: int, out_tok: int) -> str:
    p = _MODEL_PRICING.get(model)
    if not p:
        return f"in={in_tok} out={out_tok} cost=?"
    cost = (in_tok * p[0] + out_tok * p[1]) / 1_000_000
    return f"in={in_tok} out={out_tok} cost=${cost:.4f}"


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
            max_tokens=4096,
        )
        u = getattr(resp, "usage", None)
        if u:
            logger.info(f"groq[{self.model}] {_estimate_cost(self.model, u.prompt_tokens or 0, u.completion_tokens or 0)}")
        return resp.choices[0].message.content


class OpenRouterProvider(LLMProvider):
    """OpenAI-compatible client pointed at OpenRouter so we can use Claude / GPT-4 / etc.
    on a single key. Defaults to anthropic/claude-sonnet-4.5 — strong viral judgment,
    way better than llama for nuanced hook generation, ~10x cheaper than Opus."""
    def __init__(self):
        from openai import OpenAI
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self.client = OpenAI(
            api_key=config.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": config.OPENROUTER_REFERER,
                "X-Title": config.OPENROUTER_TITLE,
            },
        )
        self.model = config.OPENROUTER_MODEL

    def complete(self, system: str, user: str) -> str:
        # Not every model on OpenRouter supports response_format=json_object,
        # but the major ones (Claude 3.5+, GPT-4o, Gemini 1.5+) do. Pass it
        # through; if a chosen model rejects it, the user can switch model.
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=4096,
        )
        u = getattr(resp, "usage", None)
        if u:
            logger.info(
                f"openrouter[{self.model}] "
                f"{_estimate_cost(self.model, u.prompt_tokens or 0, u.completion_tokens or 0)}"
            )
        return resp.choices[0].message.content


def get_provider() -> LLMProvider:
    p = config.LLM_PROVIDER
    if p == "ollama":
        return OllamaProvider()
    if p == "groq":
        return GroqProvider()
    if p in ("openrouter", "or"):
        return OpenRouterProvider()
    raise ValueError(f"unknown LLM_PROVIDER: {p}")


def _validate_clips(data: dict, total_duration: float) -> tuple[list[dict], list[str]]:
    clips = data.get("clips") or []
    valid: list[dict] = []
    rejected: list[str] = []
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
        emojis_raw = c.get("emojis") or []
        emojis: list[dict] = []
        for e in emojis_raw:
            if not isinstance(e, dict):
                continue
            word = str(e.get("word") or "").strip().lower()
            emo = str(e.get("emoji") or "").strip()
            if word and emo:
                emojis.append({"word": word, "emoji": emo})
        valid.append({
            "start": start,
            "end": end,
            "hook": str(c.get("hook") or "").strip()[:120],
            "caption": str(c.get("caption") or "").strip(),
            "hashtags": [str(h).lstrip("#").strip() for h in (c.get("hashtags") or []) if h],
            "score": float(c.get("score") or 0),
            "emojis": emojis,
        })
    valid.sort(key=lambda x: x["score"], reverse=True)

    # Enforce time-spread: drop clips whose start is within 90s of an already-kept clip.
    spread: list[dict] = []
    spread_rejected: list[str] = []
    SPREAD_GAP = 90.0
    for clip in valid:
        too_close = next(
            (k for k in spread if abs(clip["start"] - k["start"]) < SPREAD_GAP),
            None,
        )
        if too_close:
            spread_rejected.append(
                f"too-close-to-{too_close['start']:.0f}s:{clip['start']:.0f}s"
            )
            continue
        spread.append(clip)
        if len(spread) >= config.CLIP_COUNT_MAX:
            break

    return spread, rejected + spread_rejected


def _is_rate_limit(err: Exception) -> bool:
    msg = str(err).lower()
    return (
        "rate_limit" in msg
        or "tokens per minute" in msg
        or "tpm" in msg
        or "request too large" in msg
        or " 413" in msg
    )


def analyze(transcript: dict) -> list[dict]:
    provider = get_provider()
    logger.info(f"Analyzing with {config.LLM_PROVIDER} ({type(provider).__name__})")

    system = SYSTEM_PROMPT.format(
        min_sec=config.CLIP_MIN_SEC,
        max_sec=config.CLIP_MAX_SEC,
        cmin=config.CLIP_COUNT_MIN,
        cmax=config.CLIP_COUNT_MAX,
    )
    # Start budget — providers with bigger TPM caps (OpenRouter / Claude / GPT-4) can use more.
    token_budget = 24000 if config.LLM_PROVIDER in ("openrouter", "or") else 6500
    user = _build_user_prompt(transcript, target_input_tokens=token_budget)

    last_err: Exception | None = None
    last_rejected: list[str] = []
    last_returned_count = -1
    raw = ""
    for attempt in range(1, 4):
        try:
            if attempt == 1:
                raw = provider.complete(system, user)
            elif _is_rate_limit(last_err) if last_err else False:
                # Halve budget and rebuild — earlier attempt blew the TPM cap.
                token_budget = max(1500, token_budget // 2)
                logger.info(f"rate-limit retry: shrinking transcript to ~{token_budget} input tokens")
                user = _build_user_prompt(transcript, target_input_tokens=token_budget)
                raw = provider.complete(system, user)
            elif last_returned_count == 0:
                fix_user = (
                    f"Your previous response was valid JSON but contained ZERO clips.\n"
                    f"That's not acceptable — this transcript is {transcript['duration']:.0f}s long, "
                    f"there are absolutely viral-worthy moments in here.\n\n"
                    f"Lower your bar. Pick {config.CLIP_COUNT_MIN}-{config.CLIP_COUNT_MAX} of the BEST "
                    f"{config.CLIP_MIN_SEC}-{config.CLIP_MAX_SEC}s windows even if none feel perfect. "
                    f"A 60/100 score is fine.\n\n"
                    f"Original request:\n{user}"
                )
                raw = provider.complete(system, fix_user)
            elif last_rejected:
                fix_user = (
                    f"Your previous response had {len(last_rejected)} clip(s) but none passed validation.\n"
                    f"Reasons: {last_rejected[:3]}\n\n"
                    f"Constraints reminder:\n"
                    f"- Video duration: {transcript['duration']:.1f}s\n"
                    f"- Each clip MUST be between {config.CLIP_MIN_SEC} and {config.CLIP_MAX_SEC} seconds long.\n"
                    f"- 'start' and 'end' must be within [0, {transcript['duration']:.1f}] (in seconds).\n\n"
                    f"Original request:\n{user}"
                )
                raw = provider.complete(system, fix_user)
            else:
                fix_user = (
                    f"Your previous response could not be parsed as JSON. Error: {last_err}\n\n"
                    f"Previous response:\n{raw[:400]}\n\n"
                    "Return ONLY valid JSON matching the schema. No markdown. No prose."
                )
                raw = provider.complete(system, fix_user)
            data = _extract_json(raw)
            last_returned_count = len(data.get("clips") or [])
            clips, rejected = _validate_clips(data, transcript["duration"])
            last_rejected = rejected
            if not clips:
                logger.warning(f"raw response: {raw[:800]}")
                raise ValueError(
                    f"no valid clips (returned {last_returned_count}, "
                    f"rejected {len(rejected)}: {rejected[:3]})"
                )
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
