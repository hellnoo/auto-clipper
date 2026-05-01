"""Weekly AI improvement bot.

Runs in GitHub Actions on a cron. Reads the codebase, asks Claude (via
OpenRouter) for 1-3 SAFE small improvements, applies the diff, and exits
with a non-zero code if nothing changed (so the workflow can decide
whether to open a PR).

Constraints baked into the prompt:
  - Only one focused improvement per file
  - No new dependencies
  - No major refactors
  - Must include 1-line rationale per change in proposal.md
  - Must not touch credentials / OAuth / billing code
"""
from __future__ import annotations

import os
import re
import sys
import json
import textwrap
from pathlib import Path

import openai  # type: ignore


REPO_ROOT = Path(__file__).resolve().parent.parent
PROPOSAL_PATH = REPO_ROOT / "ai_proposal.md"

# Files we DO let the bot touch
TARGET_GLOBS = [
    "src/*.py",
    "src/uploaders/*.py",
    "dashboard/*.py",
]
# Files we PROTECT
SKIP_PATTERNS = [
    re.compile(r".*config\.py$"),       # env loader, sensitive
    re.compile(r".*uploaders/.*\.py$"), # auth code, careful
]


SYSTEM = textwrap.dedent("""
    You are a senior code reviewer doing a small weekly polish pass on
    a Python project called auto-clipper. The project pipeline is:
    yt-dlp → faster-whisper → Claude → ffmpeg → social uploaders.

    Pick **1 to 3 small, isolated improvements**. Examples of good moves:
      - Replace a magic number with a named constant
      - Add a missing type hint
      - Tighten an error message that's too vague to debug
      - Remove dead code (variable assigned but never used)
      - Fix an obvious bug introduced by a recent commit
      - Add a docstring to a public function that's missing one

    HARD RULES:
      - NO new dependencies
      - NO API/contract changes (function signatures must stay backward-compatible)
      - NO credential/OAuth/billing code edits
      - NO speculative refactors
      - Each change must be self-evident and easily reviewable

    Output STRICT JSON:
    {
      "rationale": "one paragraph summarising what you changed and why",
      "changes": [
        {
          "path": "src/some_file.py",
          "find":  "<exact text block to replace, ≥ 20 chars, must be unique in the file>",
          "replace": "<replacement text>",
          "why": "one-line rationale"
        }
      ]
    }

    If you don't see anything worth a PR this week, return {"changes": [], "rationale": "no changes warranted"}.
""").strip()


def _load_target_files() -> dict[str, str]:
    out: dict[str, str] = {}
    for pat in TARGET_GLOBS:
        for p in REPO_ROOT.glob(pat):
            rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
            if any(skip.match(rel) for skip in SKIP_PATTERNS):
                continue
            try:
                out[rel] = p.read_text(encoding="utf-8")
            except Exception:
                continue
    return out


def _ask_claude(files: dict[str, str]) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://github.com/hellnoo/auto-clipper",
            "X-Title": "auto-clipper-improver",
        },
    )

    # Concatenate files with clear delimiters
    chunks: list[str] = []
    for path, content in files.items():
        chunks.append(f"# === FILE: {path} ===\n{content}\n")
    user_msg = "\n".join(chunks)
    # Clip to a sane budget — Claude Sonnet handles ~200K but we don't need that much
    if len(user_msg) > 80_000:
        user_msg = user_msg[:80_000] + "\n# ... (truncated)\n"

    resp = client.chat.completions.create(
        model="anthropic/claude-sonnet-4.5",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=4096,
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


def _apply_changes(plan: dict) -> int:
    applied = 0
    for ch in plan.get("changes") or []:
        path = REPO_ROOT / ch["path"]
        if not path.exists():
            print(f"  skip {ch['path']}: file missing")
            continue
        text = path.read_text(encoding="utf-8")
        find = ch["find"]
        replace = ch["replace"]
        # Safety: 'find' must occur exactly once
        if text.count(find) != 1:
            print(f"  skip {ch['path']}: find-block not uniquely matched ({text.count(find)} occurrences)")
            continue
        path.write_text(text.replace(find, replace, 1), encoding="utf-8")
        print(f"  applied: {ch['path']}  ({ch.get('why', 'no rationale')})")
        applied += 1
    return applied


def _write_proposal(plan: dict, applied_count: int) -> None:
    rationale = plan.get("rationale") or ""
    changes = plan.get("changes") or []
    md_lines = [
        "# 🤖 Weekly AI improvements",
        "",
        rationale,
        "",
        f"Applied **{applied_count} of {len(changes)}** proposed change(s).",
        "",
        "## Proposed changes",
        "",
    ]
    for ch in changes:
        md_lines.append(f"### `{ch['path']}`")
        md_lines.append(f"_{ch.get('why', '')}_")
        md_lines.append("")
    md_lines.append("---")
    md_lines.append("Reviewed by a human before merge. Bot can be disabled by")
    md_lines.append("removing `.github/workflows/auto-improve.yml`.")
    PROPOSAL_PATH.write_text("\n".join(md_lines), encoding="utf-8")


def main() -> int:
    print("loading target files...")
    files = _load_target_files()
    print(f"  {len(files)} files in scope")
    if not files:
        print("nothing to scan")
        return 1

    print("asking Claude for improvements...")
    plan = _ask_claude(files)

    print(f"plan: {len(plan.get('changes') or [])} change(s) proposed")
    applied = _apply_changes(plan)

    if applied == 0:
        print("no applicable changes; exiting")
        return 1

    _write_proposal(plan, applied)
    print(f"wrote proposal -> {PROPOSAL_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
