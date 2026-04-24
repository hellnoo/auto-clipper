from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from src import db, config

app = FastAPI(title="Auto-Clipper Dashboard")
app.mount("/media", StaticFiles(directory=str(config.OUTPUT_DIR)), name="media")


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Auto-Clipper</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:0;background:#111;color:#eee}}
 header{{padding:16px 24px;background:#000;border-bottom:1px solid #333}}
 h1{{margin:0;font-size:20px}}
 main{{padding:24px;max-width:1200px;margin:0 auto}}
 .video{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin-bottom:16px}}
 .video h2{{margin:0 0 8px;font-size:16px}}
 .meta{{font-size:12px;color:#888;margin-bottom:12px}}
 .clips{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}}
 .clip{{background:#222;border:1px solid #333;border-radius:6px;padding:10px}}
 .clip video{{width:100%;border-radius:4px;background:#000}}
 .hook{{font-weight:600;margin:8px 0 4px;font-size:13px}}
 .caption{{font-size:12px;color:#bbb}}
 .tags{{font-size:11px;color:#6af;margin-top:6px}}
 .status{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:#333}}
 .status.done{{background:#063;color:#6f9}}
 .status.error{{background:#600;color:#f99}}
 .empty{{color:#666;text-align:center;padding:40px}}
</style></head>
<body>
<header><h1>Auto-Clipper Dashboard</h1></header>
<main>{body}</main>
</body></html>
"""


def _media_url(abs_path: str | None) -> str | None:
    if not abs_path:
        return None
    try:
        rel = Path(abs_path).resolve().relative_to(config.OUTPUT_DIR.resolve())
        return f"/media/{rel.as_posix()}"
    except ValueError:
        return None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    videos = db.list_videos()
    if not videos:
        return PAGE.format(body='<div class="empty">No videos yet. Run <code>python -m src.main --url ...</code></div>')

    blocks = []
    for v in videos:
        clips = db.list_clips(v["id"])
        clip_html = []
        for c in clips:
            url = _media_url(c["path"])
            video_tag = f'<video src="{url}" controls preload="metadata"></video>' if url else '<div style="padding:20px;text-align:center;color:#666">no file</div>'
            tags = " ".join(f"#{t}" for t in (c["hashtags"] or "").split(",") if t)
            st = c["status"] or "pending"
            st_class = "done" if st == "done" else ("error" if st.startswith("error") else "")
            clip_html.append(
                f'<div class="clip">{video_tag}'
                f'<div class="hook">{c["hook"] or ""}</div>'
                f'<div class="caption">{c["caption"] or ""}</div>'
                f'<div class="tags">{tags}</div>'
                f'<div style="margin-top:6px"><span class="status {st_class}">{st}</span> '
                f'<span style="font-size:11px;color:#888">score: {c["score"] or 0:.0f} | {c["start_sec"]:.1f}-{c["end_sec"]:.1f}s</span></div>'
                f'</div>'
            )
        st = v["status"] or "pending"
        st_class = "done" if st == "done" else ("error" if st == "error" else "")
        body = (
            f'<div class="video"><h2>{v["title"] or v["url"]}</h2>'
            f'<div class="meta">'
            f'<span class="status {st_class}">{st}</span> | '
            f'lang: {v["language"] or "?"} | '
            f'{v["duration"] or 0:.0f}s | '
            f'<a href="{v["url"]}" target="_blank" style="color:#6af">source</a>'
            f'</div>'
            f'<div class="clips">{"".join(clip_html) or "<div style=color:#666>no clips</div>"}</div>'
            f'</div>'
        )
        blocks.append(body)
    return PAGE.format(body="".join(blocks))


@app.get("/api/videos")
def api_videos() -> dict:
    return {"videos": db.list_videos()}


@app.get("/api/videos/{video_id}/clips")
def api_clips(video_id: int) -> dict:
    v = db.get_video(video_id)
    if not v:
        raise HTTPException(404)
    return {"video": v, "clips": db.list_clips(video_id)}
