import subprocess
import shutil
from pathlib import Path
from loguru import logger
from . import config
from . import cropper

PHRASE_SIZE = 3
HOOK_DURATION = 2.5
CAPTION_GRACE = 0.10   # buffer between hook end and caption start
LINE_WRAP_CHARS = 22   # wrap a phrase if it would exceed this many chars
WORD_LINGER = 0.20     # keep last word visible this long after it's sung
# Default style is karaoke-aware: SecondaryColour = unsung (white), PrimaryColour
# = sung (bright yellow). Karaoke \kf fills smoothly over each word's duration.
# MarginV 600 keeps captions above the TikTok / IG bottom-UI safe zone.
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat,84,&H0000F0FF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,8,3,2,80,80,600,1
Style: Hook,Impact,112,&H0000F0FF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,1,10,4,8,80,80,260,1
Style: Emoji,Segoe UI Emoji,140,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,4,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def check_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg not found in PATH. Install from https://ffmpeg.org/download.html "
            "and ensure it's on your system PATH."
        )


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass_text(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\\", "/")


def _normalize_word(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def _capitalize(text: str) -> str:
    if not text:
        return text
    # Find first letter and uppercase it (handles leading punctuation/quotes)
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i+1:]
    return text


def generate_ass(
    words: list[dict],
    out_path: Path,
    hook: str | None = None,
    emojis: list[dict] | None = None,
) -> None:
    dialogues: list[str] = []

    if hook:
        hook_text = _escape_ass_text(hook.strip())
        # Pop-in scale animation + fade for impact.
        anim = r"{\fad(120,300)\t(0,200,\fscx110\fscy110)\t(200,400,\fscx100\fscy100)}"
        dialogues.append(
            f"Dialogue: 1,{_ass_time(0.0)},{_ass_time(HOOK_DURATION)},Hook,,0,0,0,,"
            + anim + hook_text
        )

    # Emojis only fire after the hook is gone, so they don't fight for attention.
    if emojis:
        emoji_map: dict[str, str] = {}
        for e in emojis:
            nw = _normalize_word(e.get("word") or "")
            if nw and nw not in emoji_map:
                emoji_map[nw] = e.get("emoji", "")
        emitted = 0
        used_norms: set[str] = set()
        for w in words:
            if w["start"] < HOOK_DURATION + CAPTION_GRACE:
                continue
            nw = _normalize_word(w["word"])
            if nw in emoji_map and nw not in used_norms:
                emo = emoji_map[nw]
                start_t = max(0.0, w["start"] - 0.05)
                end_t = w["end"] + 1.4
                pop = r"{\fad(120,300)\t(0,180,\fscx115\fscy115)\t(180,320,\fscx100\fscy100)\an5\pos(540,720)}"
                dialogues.append(
                    f"Dialogue: 2,{_ass_time(start_t)},{_ass_time(end_t)},Emoji,,0,0,0,,"
                    + pop + emo
                )
                used_norms.add(nw)
                emitted += 1
                if emitted >= 8:
                    break

    # Karaoke-fill captions, only after the hook period.
    cap_start = HOOK_DURATION + CAPTION_GRACE
    visible = [w for w in words if w["start"] >= cap_start]

    for i in range(0, len(visible), PHRASE_SIZE):
        phrase = visible[i : i + PHRASE_SIZE]
        if not phrase:
            continue
        line_start = phrase[0]["start"]
        line_end = phrase[-1]["end"] + WORD_LINGER

        # Build karaoke text with word-wrap (\N) when a line gets too long.
        rendered: list[str] = []
        running = 0
        for j, w in enumerate(phrase):
            raw = w["word"].strip()
            txt = _escape_ass_text(raw)
            if j == 0:
                txt = _capitalize(txt)
            dur_cs = max(1, int(round((w["end"] - w["start"]) * 100)))
            # Wrap before adding if this word would push past the limit.
            if running and running + len(txt) + 1 > LINE_WRAP_CHARS:
                rendered.append(r"\N")
                running = 0
            if running and not rendered[-1].endswith(r"\N"):
                rendered.append(" ")
                running += 1
            rendered.append(f"{{\\kf{dur_cs}}}{txt}")
            running += len(txt)
        text = "".join(rendered)
        dialogues.append(
            f"Dialogue: 0,{_ass_time(line_start)},{_ass_time(line_end)},Default,,0,0,0,,{text}"
        )

    out_path.write_text(ASS_HEADER + "\n".join(dialogues) + "\n", encoding="utf-8")


def _clip_words(all_words: list[dict], start: float, end: float) -> list[dict]:
    out: list[dict] = []
    for w in all_words:
        if w["end"] <= start or w["start"] >= end:
            continue
        out.append({
            "start": max(0.0, w["start"] - start),
            "end": max(0.0, min(end, w["end"]) - start),
            "word": w["word"],
        })
    return out


# --- Silence-cut helpers ---
# Detect gaps in word timestamps > GAP_THRESHOLD; remove them so the clip
# stays high-energy. Pads kept around each speech run so cuts don't sound
# clipped.
GAP_THRESHOLD = 0.70  # seconds of silence to be worth cutting (conservative — keep
                      # natural beats and short pauses; only chop genuinely dead air)
SPEECH_PAD = 0.18     # keep this much silence around each kept run so cuts don't
                      # sound abrupt and dramatic pauses still land


def _speech_keeps(words: list[dict], clip_dur: float) -> list[tuple[float, float]]:
    """Return list of (start, end) intervals in clip-relative seconds to KEEP."""
    if not words:
        return [(0.0, clip_dur)]
    keeps: list[list[float]] = []
    for w in words:
        s = max(0.0, w["start"] - SPEECH_PAD)
        e = min(clip_dur, w["end"] + SPEECH_PAD)
        if keeps and s <= keeps[-1][1] + 0.01:
            keeps[-1][1] = max(keeps[-1][1], e)
        else:
            keeps.append([s, e])
    # Filter: only count a "cut" if the gap before the next run is > threshold.
    merged: list[list[float]] = []
    for k in keeps:
        if not merged:
            merged.append(k)
            continue
        gap = k[0] - merged[-1][1]
        if gap < GAP_THRESHOLD:
            merged[-1][1] = k[1]
        else:
            merged.append(k)
    return [(s, e) for s, e in merged]


def _remap_words_after_cuts(words: list[dict], keeps: list[tuple[float, float]]) -> list[dict]:
    """Translate word timestamps from original clip-time to post-cut clip-time."""
    if not keeps:
        return words
    # Cumulative time removed before each keep range.
    out: list[dict] = []
    for w in words:
        # Find which keep-range this word belongs to.
        elapsed = 0.0
        for s, e in keeps:
            if w["start"] >= s and w["end"] <= e:
                ws = w["start"] - s + elapsed
                we = w["end"] - s + elapsed
                out.append({"start": ws, "end": we, "word": w["word"]})
                break
            elapsed += e - s
    return out


def _build_select_expr(keeps: list[tuple[float, float]]) -> str:
    """ffmpeg select filter expression: between(t,a,b)+between(t,c,d)+..."""
    parts = [f"between(t,{s:.3f},{e:.3f})" for s, e in keeps]
    return "+".join(parts) if parts else "1"


def render_clip(source_path: str, clip: dict, words: list[dict], out_path: Path) -> Path:
    check_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = out_path.with_suffix(".ass")

    duration = clip["end"] - clip["start"]
    clip_words = _clip_words(words, clip["start"], clip["end"])

    # Face-aware crop: detect speaker's horizontal position, fall back to center.
    face_cx = cropper.detect_face_center_x(source_path, clip["start"], clip["end"])
    if face_cx is not None:
        # crop_w = min(iw, ih*9/16); x = clamp(iw*face_cx - crop_w/2, 0, iw - crop_w)
        crop_filter = (
            "crop='min(iw,ih*9/16)':ih:"
            f"'max(0,min(iw-min(iw\\,ih*9/16),iw*{face_cx:.3f}-min(iw\\,ih*9/16)/2))':0"
        )
        logger.info(f"face-aware crop: center x={face_cx:.2f}")
    else:
        crop_filter = "crop='min(iw,ih*9/16)':ih:(iw-min(iw\\,ih*9/16))/2:0"

    # Detect silences and rebuild keep-ranges. If we can save >0.5s by cutting,
    # use the select+setpts filter chain; otherwise stay simple.
    keeps = _speech_keeps(clip_words, duration)
    kept_dur = sum(e - s for s, e in keeps)
    use_silence_cut = (duration - kept_dur) > 0.5 and len(keeps) >= 2

    if use_silence_cut:
        clip_words = _remap_words_after_cuts(clip_words, keeps)
        select_expr = _build_select_expr(keeps)
        select_chain = f"select='{select_expr}',setpts=N/FRAME_RATE/TB,"
        aselect_chain = f"aselect='{select_expr}',asetpts=N/SR/TB,"
        out_dur = kept_dur
        logger.info(
            f"silence-cut: {duration:.1f}s -> {kept_dur:.1f}s "
            f"({len(keeps)} runs, saved {duration-kept_dur:.1f}s)"
        )
    else:
        select_chain = ""
        aselect_chain = ""
        out_dur = duration

    # Fade in/out — softer cut endings, more pro feel
    fade_d = 0.15
    fade_out_st = max(0.0, out_dur - fade_d)
    video_fade = f"fade=t=in:st=0:d={fade_d},fade=t=out:st={fade_out_st:.3f}:d={fade_d}"
    audio_fade = f"afade=t=in:st=0:d={fade_d},afade=t=out:st={fade_out_st:.3f}:d={fade_d}"

    vf = (
        f"{select_chain}"
        f"{crop_filter},"
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"subtitles={ass_path.name},"
        f"{video_fade}"
    )
    # loudnorm targets TikTok / IG: -14 LUFS integrated, -1.5 dBTP peak.
    # Single-pass is fine for short-form; the dynamic-range smoothing kicks in
    # quickly enough on 30-60 s windows.
    af = f"{aselect_chain}loudnorm=I=-14:TP=-1.5:LRA=11,{audio_fade}"

    generate_ass(clip_words, ass_path, hook=clip.get("hook"), emojis=clip.get("emojis"))

    cmd = ["ffmpeg", "-y"]
    if not use_silence_cut:
        # Fast seek BEFORE -i for non-cut clips (much faster on big sources).
        cmd += ["-ss", f"{clip['start']:.3f}", "-i", source_path, "-t", f"{duration:.3f}"]
    else:
        cmd += [
            "-ss", f"{clip['start']:.3f}",
            "-t", f"{duration:.3f}",
            "-i", source_path,
        ]
    cmd += [
        "-vf", vf,
        "-af", af,
        # 'medium' preset on 30-60s clip is still 5-15s of CPU but the quality
        # bump over veryfast is visible on text edges and gradients.
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        str(out_path.name),
    ]
    logger.info(f"ffmpeg render -> {out_path.name} ({duration:.1f}s source)")
    result = subprocess.run(cmd, cwd=out_path.parent, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(result.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode})")
    return out_path


def write_caption_file(clip: dict, out_path: Path) -> None:
    lines = [
        clip.get("hook", ""),
        "",
        clip.get("caption", ""),
        "",
        " ".join(f"#{h}" for h in (clip.get("hashtags") or [])),
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    check_ffmpeg()
    print("ffmpeg OK")
