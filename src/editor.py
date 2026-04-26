import subprocess
import shutil
from pathlib import Path
from loguru import logger
from . import config
from . import cropper

PHRASE_SIZE = 3
HOOK_DURATION = 2.5
# Caption: white text, fat black outline, soft drop shadow.
# Hook: huge bold yellow, thicker stroke, top-aligned with semi-transparent black panel feel.
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat,82,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,2,80,80,360,1
Style: Hook,Impact,108,&H0000F0FF,&H000000FF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,1,9,4,8,80,80,180,1
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


def generate_ass(
    words: list[dict],
    out_path: Path,
    hook: str | None = None,
    emojis: list[dict] | None = None,
) -> None:
    dialogues: list[str] = []
    if hook:
        # Word-wrap long hooks so they don't run off-screen.
        hook_text = _escape_ass_text(hook.strip())
        # Pop-in scale animation + fade for impact.
        anim = r"{\fad(120,300)\t(0,200,\fscx110\fscy110)\t(200,400,\fscx100\fscy100)}"
        dialogues.append(
            f"Dialogue: 1,{_ass_time(0.0)},{_ass_time(HOOK_DURATION)},Hook,,0,0,0,,"
            + anim + hook_text
        )

    # Emoji pop-ups above the captions, triggered when the cued word is spoken.
    if emojis:
        # Build lookup: normalized-word -> emoji char (first match wins).
        emoji_map = {}
        for e in emojis:
            nw = _normalize_word(e.get("word") or "")
            if nw and nw not in emoji_map:
                emoji_map[nw] = e.get("emoji", "")
        emitted = 0
        used_norms: set[str] = set()
        for w in words:
            nw = _normalize_word(w["word"])
            if nw in emoji_map and nw not in used_norms:
                emo = emoji_map[nw]
                start_t = max(0.0, w["start"] - 0.05)
                end_t = w["end"] + 1.4
                # Pop in: fade + 70%->110%->100% bounce, positioned center-top.
                pop = r"{\fad(120,300)\t(0,180,\fscx115\fscy115)\t(180,320,\fscx100\fscy100)\an5\pos(540,640)}"
                dialogues.append(
                    f"Dialogue: 2,{_ass_time(start_t)},{_ass_time(end_t)},Emoji,,0,0,0,,"
                    + pop + emo
                )
                used_norms.add(nw)
                emitted += 1
                if emitted >= 8:
                    break
    for i in range(0, len(words), PHRASE_SIZE):
        phrase = words[i : i + PHRASE_SIZE]
        if not phrase:
            continue
        for j, active in enumerate(phrase):
            parts: list[str] = []
            for k, w in enumerate(phrase):
                txt = _escape_ass_text(w["word"])
                if k == j:
                    # Active word: bright yellow, slightly bigger, bold.
                    parts.append(r"{\c&H00F0FF&\b1\fs92}" + txt + r"{\c&HFFFFFF&\b0\fs82}")
                else:
                    parts.append(txt)
            start = _ass_time(active["start"])
            end = _ass_time(active["end"])
            dialogues.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{' '.join(parts)}")
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
        vf = (
            f"select='{select_expr}',setpts=N/FRAME_RATE/TB,"
            f"{crop_filter},"
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"subtitles={ass_path.name}"
        )
        af = f"aselect='{select_expr}',asetpts=N/SR/TB"
        logger.info(
            f"silence-cut: {duration:.1f}s -> {kept_dur:.1f}s "
            f"({len(keeps)} runs, saved {duration-kept_dur:.1f}s)"
        )
    else:
        vf = (
            f"{crop_filter},"
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"subtitles={ass_path.name}"
        )
        af = None

    generate_ass(clip_words, ass_path, hook=clip.get("hook"), emojis=clip.get("emojis"))

    cmd = ["ffmpeg", "-y"]
    if not use_silence_cut:
        # Fast seek BEFORE -i for non-cut clips (much faster on big sources).
        cmd += ["-ss", f"{clip['start']:.3f}", "-i", source_path, "-t", f"{duration:.3f}"]
    else:
        # For silence-cut we need accurate seek (decode-seek), so put -ss after -i scope.
        cmd += [
            "-ss", f"{clip['start']:.3f}",
            "-t", f"{duration:.3f}",
            "-i", source_path,
        ]
    cmd += ["-vf", vf]
    if af:
        cmd += ["-af", af]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
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
