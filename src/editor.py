import subprocess
import shutil
from pathlib import Path
from loguru import logger
from . import config

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


def render_clip(source_path: str, clip: dict, words: list[dict], out_path: Path) -> Path:
    check_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = out_path.with_suffix(".ass")
    clip_words = _clip_words(words, clip["start"], clip["end"])
    generate_ass(clip_words, ass_path, hook=clip.get("hook"), emojis=clip.get("emojis"))

    duration = clip["end"] - clip["start"]
    vf = (
        "crop='min(iw,ih*9/16)':ih:(iw-min(iw\\,ih*9/16))/2:0,"
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"subtitles={ass_path.name}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{clip['start']:.3f}",
        "-i", source_path,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path.name),
    ]
    logger.info(f"ffmpeg render -> {out_path.name} ({duration:.1f}s)")
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
