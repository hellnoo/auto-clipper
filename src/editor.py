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
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Bangers,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,1,6,2,8,100,100,140,1
Style: Hook,Impact,88,&H0000F0FF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,1,8,4,2,50,50,420,1
Style: Emoji,Segoe UI Emoji,160,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,6,5,0,0,0,1
Style: Watermark,Permanent Marker,80,&HB0FFFFFF,&H000000FF,&HB0000000,&H00000000,0,0,0,0,100,100,0,0,1,3,2,5,0,0,0,1
Style: EndCard,Bangers,84,&H0000F0FF,&H00FFFFFF,&H00000000,&HC0000000,0,0,0,0,100,100,0,0,1,7,3,2,100,100,360,1

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


# Conservative filler list — these are real disfluencies, not loaded words.
# Skipping ID particles like "kan", "ya", "lo", "gue" because they can be
# meaning-bearing and audio still says them either way.
_FILLERS = {
    "um", "uhm", "uh", "uhh", "uhhh",
    "eh", "ehh", "ehm", "em", "emm",
    "ah", "ahh", "oh", "ohh",
    "hmm", "hmmm", "mm", "mmm",
}


def _is_filler(word: str) -> bool:
    norm = "".join(c for c in word.lower() if c.isalnum())
    return norm in _FILLERS


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
    clip_duration: float | None = None,
    watermark: str | None = None,
    cta: str | None = None,
) -> None:
    dialogues: list[str] = []

    # Watermark first (lowest layer) so it sits behind everything else.
    # Per-call watermark beats the global config default; pass empty string
    # explicitly to disable.
    wm = watermark if watermark is not None else config.WATERMARK_TEXT
    if wm and wm.strip():
        wm_text = _escape_ass_text(wm.strip())
        wm_end = clip_duration if clip_duration else 9999.0
        # Centered, transparent, slight tilt — like a photo watermark stamp.
        # \an5\pos(540,1620) anchors center at lower-mid of frame (above
        # bottom UI but below hook). \frz=-8 gives subtle handwritten tilt.
        dialogues.append(
            f"Dialogue: 0,{_ass_time(HOOK_DURATION + 0.2)},{_ass_time(wm_end)},Watermark,,0,0,0,,"
            + r"{\fad(400,300)\an5\pos(540,1620)\frz-8}" + wm_text
        )

    if hook:
        # Auto-fit hook so it DOMINATES the frame. Rules: short hooks stay
        # huge (1-2 lines at 88pt). Long hooks split into 3 lines so each
        # line is short enough to use a still-large font instead of shrinking
        # text into oblivion.
        #   < 16 chars   -> 1 line at 88pt
        #   16-28 chars  -> 2 lines at 88pt
        #   29-44 chars  -> 2 lines at 80pt
        #   45-66 chars  -> 3 lines at 80pt   (was 64pt / 2 lines)
        #   > 66 chars   -> 3 lines at 70pt + truncate at 80
        cleaned = hook.strip().rstrip(".!?,")
        if len(cleaned) > 80:
            cleaned = cleaned[:77].rsplit(" ", 1)[0] + "…"
        n = len(cleaned)

        def _split_n_lines(text: str, lines: int) -> str:
            """Split into N near-equal-length lines at word boundaries."""
            words_split = text.split()
            if lines <= 1 or len(words_split) <= 1:
                return _escape_ass_text(text)
            total = len(text)
            chunk_target = total / lines
            out_lines: list[str] = []
            buf: list[str] = []
            buf_len = 0
            for w in words_split:
                proj = buf_len + len(w) + (1 if buf else 0)
                if buf and len(out_lines) < lines - 1 and proj > chunk_target:
                    out_lines.append(" ".join(buf))
                    buf = [w]
                    buf_len = len(w)
                    chunk_target = (total - sum(len(s) + 1 for s in out_lines)) / (lines - len(out_lines))
                else:
                    buf.append(w)
                    buf_len = proj
            if buf:
                out_lines.append(" ".join(buf))
            return r"\N".join(_escape_ass_text(l) for l in out_lines)

        # ALL CAPS for hook — viral comedy-shorts standard.
        cleaned_caps = cleaned.upper()
        if n < 16:
            hook_size_override = ""
            hook_text = _escape_ass_text(cleaned_caps)
        else:
            if n <= 28:
                fs, lines = 88, 2
            elif n <= 44:
                fs, lines = 80, 2
            elif n <= 66:
                fs, lines = 80, 3
            else:
                fs, lines = 70, 3
            hook_size_override = rf"\fs{fs}"
            # Multi-color per line: line 1 WHITE, lines 2+ YELLOW (Submagic /
            # comedycloopsid style). Inject \1c overrides between line breaks.
            split_text = _split_n_lines(cleaned_caps, lines)
            line_parts = split_text.split(r"\N")
            recolored = []
            for li, lt in enumerate(line_parts):
                color = "&H00FFFFFF&" if li == 0 else "&H0000F0FF&"
                recolored.append(rf"{{\1c{color}}}" + lt)
            hook_text = r"\N".join(recolored)
        # Layered animation:
        #   - pop in scale 30% -> 115% -> 100%
        #   - fade in / fade out
        #   - color sweep: white -> cyan -> magenta over the hook duration
        # Color hex is &HBBGGRR. Cyan ≈ &H00F0FF (BGR), Magenta ≈ &HFF66E0.
        # Hook position: lower-middle of the 9:16 frame (~y=1300 in 1920 height).
        # \an5 = anchor at text center, \pos places that center at the
        # specified point — gives consistent layout regardless of line count.
        hook_x = 540
        hook_y = 1300
        hook_t_exit_start = int((HOOK_DURATION - 0.4) * 1000)
        hook_t_exit_end = int(HOOK_DURATION * 1000)
        # Pop-in scale + slide up exit. Per-line colors handled in hook_text.
        anim = (
            r"{\fad(140,350)"
            + hook_size_override +
            f"\\an5\\move({hook_x},{hook_y},{hook_x},{hook_y - 60},"
            f"{hook_t_exit_start},{hook_t_exit_end})"
            r"\fscx30\fscy30"
            r"\t(0,180,\fscx115\fscy115)"
            r"\t(180,320,\fscx100\fscy100)"
            r"}"
        )
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
        # Position scatter — alternate around center so emojis don't pile up.
        # Coords are in PlayRes (1080×1920). Y range 640-820 sits comfortably
        # between the hook line and the caption block.
        scatter = [
            (540, 720),  # dead center
            (420, 690),  # left, slightly up
            (660, 730),  # right, slightly down
            (510, 780),  # center-low
            (570, 660),  # center-high
            (390, 770),  # far left, low
            (690, 680),  # far right, high
            (540, 740),  # center
            (450, 720),  # left
            (630, 720),  # right
        ]
        for w in words:
            if w["start"] < HOOK_DURATION + CAPTION_GRACE:
                continue
            nw = _normalize_word(w["word"])
            if nw in emoji_map and nw not in used_norms:
                emo = emoji_map[nw]
                start_t = max(0.0, w["start"] - 0.05)
                end_t = w["end"] + 1.6
                px, py = scatter[emitted % len(scatter)]
                rot_in = -12 if emitted % 2 == 0 else 12
                # Rotate through 4 entrance styles for variety. All settle to
                # 100% scale at center-anchored position with idle breathing.
                style = emitted % 4
                if style == 0:
                    # Bounce: tiny -> overshoot -> settle, with tilt
                    entry = (
                        rf"\an5\pos({px},{py})"
                        rf"\fscx30\fscy30\frz{rot_in}"
                        rf"\t(0,160,\fscx145\fscy145\frz0)"
                        r"\t(160,300,\fscx100\fscy100)"
                    )
                elif style == 1:
                    # Spin: scale 60%->100% with full -180° spin
                    entry = (
                        rf"\an5\pos({px},{py})"
                        r"\fscx60\fscy60\frz-180"
                        r"\t(0,320,\fscx108\fscy108\frz0)"
                        r"\t(320,480,\fscx100\fscy100)"
                    )
                elif style == 2:
                    # Drop from above: \move down + tiny squash on landing
                    drop_from = max(80, py - 240)
                    entry = (
                        rf"\an5\fscy85\fscx115"
                        rf"\move({px},{drop_from},{px},{py},0,260)"
                        r"\t(260,360,\fscx95\fscy120)"
                        r"\t(360,460,\fscx100\fscy100)"
                    )
                else:
                    # Rocket up from below
                    rise_from = min(1700, py + 240)
                    entry = (
                        rf"\an5\fscx100\fscy100"
                        rf"\move({px},{rise_from},{px},{py},0,300)"
                        r"\t(300,420,\fscx110\fscy110)"
                        r"\t(420,540,\fscx100\fscy100)"
                    )
                anim = (
                    r"{\fad(150,400)"
                    + entry
                    + r"\t(900,1500,\fscx110\fscy110)"
                    + r"\t(1500,2000,\fscx100\fscy100)"
                    + r"}"
                )
                dialogues.append(
                    f"Dialogue: 2,{_ass_time(start_t)},{_ass_time(end_t)},Emoji,,0,0,0,,"
                    + anim + emo
                )
                used_norms.add(nw)
                emitted += 1
                if emitted >= 10:
                    break

    # Captions: per-word pop-in build-up. Auto-speech captions run the WHOLE
    # clip — including during the hook (0–2.5s). Hook owns the lower-mid
    # frame, captions sit at the top, both visible together for the intro.
    visible = [w for w in words if not _is_filler(w["word"])]

    # Per-turn color palette.
    TURN_COLORS = [
        "&H0000F0FF&",  # bright yellow
        "&H00FFE066&",  # pale cyan
        "&H0066FF66&",  # mint green
        "&H00FF66E0&",  # pink
    ]
    DIM_COLOR = "&H00C0C0C0&"  # already-said words

    seen_keys: dict[str, int] = {}

    def turn_color_for(word: dict) -> str:
        key = word.get("speaker") or f"seg:{word.get('seg', 0)}"
        if key not in seen_keys:
            seen_keys[key] = len(seen_keys)
        return TURN_COLORS[seen_keys[key] % len(TURN_COLORS)]

    # Group into phrases of PHRASE_SIZE words.
    phrases: list[list[dict]] = []
    for i in range(0, len(visible), PHRASE_SIZE):
        chunk = visible[i : i + PHRASE_SIZE]
        if chunk:
            phrases.append(chunk)

    # Per-word pop animation tag (from base 80% to 115% to 100% over ~180ms).
    POP_ANIM = r"\t(0,90,\fscx115\fscy115)\t(90,180,\fscx100\fscy100)"

    for p_idx, phrase in enumerate(phrases):
        color = turn_color_for(phrase[0])
        # Where this phrase as a whole ends: next phrase start, or last
        # word + linger.
        if p_idx + 1 < len(phrases):
            phrase_end = phrases[p_idx + 1][0]["start"] - 0.02
        else:
            phrase_end = phrase[-1]["end"] + WORD_LINGER
        # Each word inside emits its own Dialogue covering [w.start, next_word.start]
        # (or phrase_end for the last word). Text is the cumulative phrase up to
        # and including this word, with the new word popped + colored, prior
        # words dimmed.
        for w_idx, w in enumerate(phrase):
            dlg_start = w["start"]
            if w_idx + 1 < len(phrase):
                dlg_end = phrase[w_idx + 1]["start"] - 0.01
            else:
                dlg_end = phrase_end
            if dlg_end - dlg_start < 0.05:
                dlg_end = dlg_start + 0.05

            parts: list[str] = []
            running = 0
            for j in range(w_idx + 1):
                raw_j = phrase[j]["word"].strip()
                txt_j = _escape_ass_text(raw_j)
                if j == 0:
                    txt_j = _capitalize(txt_j)
                if running and running + len(txt_j) + 1 > LINE_WRAP_CHARS:
                    parts.append(r"\N")
                    running = 0
                if running and not parts[-1].endswith(r"\N"):
                    parts.append(" ")
                    running += 1
                if j < w_idx:
                    parts.append(f"{{\\1c{DIM_COLOR}\\fscx100\\fscy100}}{txt_j}")
                else:
                    parts.append(f"{{\\1c{color}\\fscx80\\fscy80{POP_ANIM}}}{txt_j}")
                running += len(txt_j)

            entrance = r"{\fad(60,0)}" if w_idx == 0 else r"{\fad(0,0)}"
            text = entrance + "".join(parts)
            dialogues.append(
                f"Dialogue: 0,{_ass_time(dlg_start)},{_ass_time(dlg_end)},Default,,0,0,0,,{text}"
            )

    # End-card CTA — last ~1.0 s of the clip. Per-clip cta from LLM beats the
    # global config default; both can be empty to skip the card entirely.
    cta_text_raw = (cta or "").strip() or (config.END_CARD_TEXT or "").strip()
    if cta_text_raw and clip_duration and clip_duration > 4.0:
        cta = cta_text_raw
        if cta:
            cta_dur = 1.2  # display this long
            cta_start = max(0.0, clip_duration - cta_dur)
            cta_end = clip_duration
            # Pop-in: scale 60%->110%->100% over 350ms, then idle
            cta_anim = (
                r"{\fad(180,250)"
                r"\fscx60\fscy60"
                r"\t(0,200,\fscx112\fscy112)"
                r"\t(200,350,\fscx100\fscy100)"
                r"}"
            )
            cta_text = _escape_ass_text(cta)
            dialogues.append(
                f"Dialogue: 1,{_ass_time(cta_start)},{_ass_time(cta_end)},EndCard,,0,0,0,,"
                + cta_anim + cta_text
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
            "seg": w.get("seg", 0),
            "speaker": w.get("speaker"),
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
    out: list[dict] = []
    for w in words:
        elapsed = 0.0
        for s, e in keeps:
            if w["start"] >= s and w["end"] <= e:
                ws = w["start"] - s + elapsed
                we = w["end"] - s + elapsed
                out.append({
                    "start": ws, "end": we, "word": w["word"],
                    "seg": w.get("seg", 0),
                    "speaker": w.get("speaker"),
                })
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
    # Gated by config.SILENCE_CUT — opt-in because select+setpts can produce
    # PTS-inconsistent output that some browsers stall on.
    if config.SILENCE_CUT:
        keeps = _speech_keeps(clip_words, duration)
        kept_dur = sum(e - s for s, e in keeps)
        use_silence_cut = (duration - kept_dur) > 0.5 and len(keeps) >= 2
    else:
        keeps = []
        kept_dur = duration
        use_silence_cut = False

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

    # Background blur during the hook moment — pushes the visual back so the
    # bright hook overlay reads first. boxblur's lr/cr are init-time
    # constants in ffmpeg, so we use 'enable' to toggle the whole filter.
    # Hard cutoff at 1.8s; the hook's own fade-out (300ms ending ~2.5s)
    # masks the transition.
    blur_chain = ""
    if config.HOOK_BLUR_BG:
        blur_chain = "boxblur=10:enable='lt(t,1.8)',"

    # Ken Burns: very subtle slow push-in. zoom factor drifts from 1.00 -> ~1.05
    # over ~60 s. Centered on the face-aware crop so we never lose the speaker.
    # ffmpeg expression escapes commas with backslash, which the f-string
    # turns from \\, into \, in the actual filter string.
    kb_chain = ""
    if config.KEN_BURNS:
        kb_chain = (
            "scale=w='1080*min(1+t*0.0009\\,1.05)':"
            "h='1920*min(1+t*0.0009\\,1.05)':eval=frame,"
            "crop=w=1080:h=1920:x='(iw-1080)/2':y='(ih-1920)/2',"
        )

    # Make our bundled fonts (Bangers / Permanent Marker / Anton) available
    # to libass. ffmpeg's filter-arg parser fights Windows drive-letter
    # colons no matter how you escape them — easier to use a relative path
    # from the cwd (output/final), which avoids the colon problem entirely.
    from . import font_setup
    fonts_dir = font_setup.ensure_fonts()
    try:
        # Relative path from out_path.parent (= cwd) to the fonts dir
        rel = Path(fonts_dir).relative_to(out_path.parent.parent)
        # The launcher chdir's to clip folder via cwd=out_path.parent, so
        # we need to walk up one and into .fonts/. e.g. '../.fonts'.
        fontsdir_filter = ("../" + rel.as_posix()).replace(" ", r"\ ")
    except ValueError:
        # Fallback: absolute path. May fail on Windows due to escaping;
        # if it does, libass will silently fall back to system fonts.
        fontsdir_filter = fonts_dir.as_posix()

    vf = (
        f"{select_chain}"
        f"{crop_filter},"
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"{blur_chain}"
        f"{kb_chain}"
        f"subtitles=./{ass_path.name}:fontsdir={fontsdir_filter},"
        f"{video_fade}"
    )
    # loudnorm targets TikTok / IG: -14 LUFS integrated, -1.5 dBTP peak.
    # Single-pass is fine for short-form; the dynamic-range smoothing kicks in
    # quickly enough on 30-60 s windows.
    af = f"{aselect_chain}loudnorm=I=-14:TP=-1.5:LRA=11,{audio_fade}"

    generate_ass(
        clip_words, ass_path,
        hook=clip.get("hook"),
        emojis=clip.get("emojis"),
        clip_duration=out_dur,
        watermark=clip.get("watermark"),
        cta=clip.get("cta"),
    )

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
        # Force a stable 30 fps output and rebuild PTS timestamps from the
        # decoded frame rate. Without this, browsers can stall on streams
        # whose container PTS jumps (e.g. after silence-cut splices).
        "-r", "30",
        "-vsync", "cfr",
        "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.0",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        # Prefix './' so a leading-dash filename (YouTube ID like '-05AS...')
        # isn't misread by ffmpeg's argv parser as a flag option.
        "./" + out_path.name,
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
