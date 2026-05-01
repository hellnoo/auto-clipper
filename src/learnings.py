"""Curator learning loop.

Each time the Critic agent refines a Curator's draft, it extracts 1-3
concise lessons describing what the Curator did wrong. Those lessons
are persisted and injected into the next Curator's system prompt as
'past mistakes to avoid'.

Result: Curator improves over time, Critic finds less to refine, the
LLM_CRITIQUE pass becomes cheaper and eventually optional.
"""
from __future__ import annotations

import json
from pathlib import Path
from loguru import logger

from . import config


LEARNINGS_PATH = Path(config.ROOT) / "config" / "curator_learnings.json"
MAX_LEARNINGS = 25     # cap: keep most recent N lessons in prompt
INJECT_TOP = 12        # only inject top N most recent into the curator prompt


def _load_raw() -> list[dict]:
    if not LEARNINGS_PATH.exists():
        return []
    try:
        return json.loads(LEARNINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def add_lessons(lessons: list[str]) -> None:
    """Append new lessons; dedupe against last 100 entries; keep most recent
    MAX_LEARNINGS in the file. Lessons are short imperative sentences."""
    if not lessons:
        return
    existing = _load_raw()
    seen = {e.get("lesson") for e in existing[-100:]}
    added = 0
    from datetime import datetime
    now = datetime.utcnow().isoformat(timespec="seconds")
    for ls in lessons:
        ls = (ls or "").strip()
        if not ls or len(ls) < 10:
            continue
        if ls in seen:
            continue
        existing.append({"lesson": ls, "ts": now})
        seen.add(ls)
        added += 1
    if not added:
        return
    existing = existing[-MAX_LEARNINGS:]
    LEARNINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        LEARNINGS_PATH.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"learning loop: +{added} lesson(s) (total: {len(existing)})")
    except Exception as e:
        logger.warning(f"learnings write failed: {e}")


def lessons_block() -> str:
    """Return a markdown bullet list of the top lessons, ready to paste into
    the curator system prompt. Empty string if none."""
    items = _load_raw()
    if not items:
        return ""
    # Most recent first
    recent = list(reversed(items))[:INJECT_TOP]
    bullets = "\n".join(f"- {e['lesson']}" for e in recent)
    return (
        "\n\n# Past lessons (the senior editor flagged these in earlier "
        "drafts — don't repeat)\n" + bullets
    )


if __name__ == "__main__":
    block = lessons_block()
    if block:
        print(block)
    else:
        print("(no lessons yet)")
