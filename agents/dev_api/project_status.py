"""GET /dev/project-status — non-invasive development status dashboard data."""

from __future__ import annotations

import os
import subprocess
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_git(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _parse_status_short(raw: str) -> tuple[list[str], list[str]]:
    modified: list[str] = []
    untracked: list[str] = []
    for line in raw.splitlines():
        if not line:
            continue
        status = line[:2]
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if status == "??":
            untracked.append(path)
        else:
            modified.append(path)
    return modified, untracked


def _tag_info(tag: str) -> dict[str, Any]:
    commit = _run_git(["rev-list", "-n", "1", tag])
    return {
        "tag": tag,
        "exists": bool(commit),
        "commit": commit[:12] if commit else "",
    }


def _is_tracked(path: str) -> bool:
    return bool(_run_git(["ls-files", "--", path]))


def _is_ignored(path: str) -> bool:
    return bool(_run_git(["check-ignore", path]))


def _effective_mode() -> str:
    requested = (os.getenv("AGENT_LLM_MODE") or "mock").strip().lower()
    if requested in {"gpt", "openai", "llm"} and os.getenv("OPENAI_API_KEY"):
        return "gpt"
    return "mock"


def build_project_status() -> dict[str, Any]:
    status_raw = _run_git(["status", "--short"])
    modified, untracked = _parse_status_short(status_raw)
    branch = _run_git(["branch", "--show-current"]) or "unknown"
    last_commit = _run_git(["log", "-1", "--pretty=format:%h %s"])
    effective_mode = _effective_mode()
    env_exists = (PROJECT_ROOT / ".env").exists()
    env_tracked = _is_tracked(".env")
    runtime_ignored = any(
        _is_ignored(path)
        for path in (
            "__pycache__",
            "agents/__pycache__",
            "generated_images",
            "generated_clips",
            "generated_outputs",
            "video_jobs",
        )
    )

    return {
        "current_phase": "Phase 4D - Music GPT",
        "git_status_summary": {
            "branch": branch,
            "last_commit": last_commit,
            "modified_files": modified,
            "untracked_files": untracked,
            "is_clean": not modified and not untracked,
        },
        "active_agents": [
            {"id": "01_topic_agent", "label": "Topic Agent", "gpt_applied": True},
            {"id": "02_story_agent", "label": "Story Agent", "gpt_applied": True},
            {"id": "03_character_agent", "label": "Character Agent", "gpt_applied": True},
            {
                "id": "05_narration_subtitle_agent",
                "label": "Narration Subtitle Agent",
                "gpt_applied": True,
            },
            {"id": "06_music_agent", "label": "Music Agent", "gpt_applied": True},
            {"id": "08_review_agent", "label": "Review Agent", "gpt_applied": True},
        ],
        "current_tasks": {
            "in_progress": ["Phase 4D Music GPT"],
            "completed": [
                "Phase 4A Topic/Story GPT",
                "Phase 4B Character GPT",
                "Phase 4C Narration/Subtitle GPT",
            ],
            "next": ["Phase 5 Architecture Cleanup"],
        },
        "llm_modes": {
            "topic_llm_mode": effective_mode,
            "story_llm_mode": effective_mode,
            "character_llm_mode": effective_mode,
            "narration_llm_mode": effective_mode,
            "subtitle_llm_mode": effective_mode,
            "music_llm_mode": effective_mode,
            "review_llm_mode": effective_mode,
            "requested_llm_mode": os.getenv("AGENT_LLM_MODE", "mock"),
        },
        "recent_checkpoints": [
            _tag_info("phase3a-complete"),
            _tag_info("phase4a-complete"),
            _tag_info("phase4b-complete"),
        ],
        "warnings": {
            "uncommitted_changes": bool(modified or untracked),
            "env_not_committed": env_exists and not env_tracked,
            "runtime_files_ignored": runtime_ignored,
        },
    }


def _list_items(items: list[Any]) -> str:
    if not items:
        return "<li>none</li>"
    return "".join(f"<li>{escape(str(item))}</li>" for item in items)


def _definition_rows(mapping: dict[str, Any]) -> str:
    rows: list[str] = []
    for key, value in mapping.items():
        if isinstance(value, list):
            rendered = f"<ul>{_list_items(value)}</ul>"
        else:
            rendered = escape(str(value))
        rows.append(f"<div><dt>{escape(str(key))}</dt><dd>{rendered}</dd></div>")
    return "".join(rows)


def render_project_status_html(status: dict[str, Any]) -> str:
    git_status = status.get("git_status_summary") or {}
    llm_modes = status.get("llm_modes") or {}
    warnings = status.get("warnings") or {}
    agents = status.get("active_agents") or []
    checkpoints = status.get("recent_checkpoints") or []

    agent_items = "".join(
        (
            "<li>"
            f"<strong>{escape(str(agent.get('id', 'agent')))}</strong>"
            f"<span>{escape(str(agent.get('label', '')))}</span>"
            f"<em>{'GPT ready' if agent.get('gpt_applied') else 'Mock only'}</em>"
            "</li>"
        )
        for agent in agents
    ) or "<li>none</li>"
    checkpoint_items = "".join(
        (
            "<li>"
            f"<strong>{escape(str(item.get('tag', 'tag')))}</strong>"
            f"<span>{'present' if item.get('exists') else 'missing'}</span>"
            f"<code>{escape(str(item.get('commit') or ''))}</code>"
            "</li>"
        )
        for item in checkpoints
    ) or "<li>none</li>"
    warning_items = "".join(
        f"<li class=\"{'is-warning' if active else 'is-ok'}\"><strong>{escape(str(key))}</strong><span>{'YES' if active else 'NO'}</span></li>"
        for key, active in warnings.items()
    ) or "<li>none</li>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Project Status</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0b1020;
      color: #e5edf7;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #0b1020; }}
    main {{ width: min(1180px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 48px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 18px; }}
    h1 {{ margin: 4px 0 0; font-size: 1.8rem; letter-spacing: 0; }}
    .eyebrow {{ margin: 0; color: #93a4ba; font-size: 0.78rem; text-transform: uppercase; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    a, button {{ color: #e5edf7; }}
    .button {{ display: inline-flex; align-items: center; min-height: 34px; padding: 0 12px; border: 1px solid #334155; background: #111827; text-decoration: none; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .card {{ min-width: 0; padding: 16px; border: 1px solid #263244; background: #111827; }}
    .card-wide {{ grid-column: span 2; }}
    h2 {{ margin: 0 0 12px; font-size: 0.95rem; letter-spacing: 0; }}
    dl {{ display: grid; gap: 8px; margin: 0; }}
    dl div {{ display: grid; grid-template-columns: minmax(110px, 0.45fr) minmax(0, 1fr); gap: 10px; }}
    dt {{ color: #9fb0c5; }}
    dd {{ min-width: 0; margin: 0; overflow-wrap: anywhere; }}
    ul {{ display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }}
    li {{ min-width: 0; padding: 8px; background: #0b1220; overflow-wrap: anywhere; }}
    li strong {{ display: block; }}
    li span, li em {{ display: block; color: #a9b8ca; font-style: normal; }}
    code {{ color: #b6e3ff; }}
    .phase {{ padding: 12px 14px; border: 1px solid #315371; background: #102033; }}
    .is-warning span {{ color: #fbbf24; }}
    .is-ok span {{ color: #86efac; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} .card-wide {{ grid-column: span 1; }} header {{ flex-direction: column; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <p class="eyebrow">Development Dashboard</p>
        <h1>Project Status</h1>
      </div>
      <nav class="actions">
        <a class="button" href="/dev/project-status">JSON</a>
        <a class="button" href="/docs">Swagger</a>
        <a class="button" href="/">Main UI</a>
      </nav>
    </header>
    <section class="phase"><strong>{escape(str(status.get("current_phase", "Unknown phase")))}</strong></section>
    <section class="grid" style="margin-top: 12px;">
      <article class="card card-wide">
        <h2>Git Status</h2>
        <dl>{_definition_rows(git_status)}</dl>
      </article>
      <article class="card">
        <h2>Warnings</h2>
        <ul>{warning_items}</ul>
      </article>
      <article class="card">
        <h2>Active Agents</h2>
        <ul>{agent_items}</ul>
      </article>
      <article class="card">
        <h2>LLM Modes</h2>
        <dl>{_definition_rows(llm_modes)}</dl>
      </article>
      <article class="card">
        <h2>Recent Checkpoints</h2>
        <ul>{checkpoint_items}</ul>
      </article>
    </section>
  </main>
</body>
</html>"""


def register_project_status_routes(app: FastAPI) -> None:
    @app.get("/dev/project-status")
    def dev_project_status() -> dict[str, Any]:
        return build_project_status()

    @app.get("/dev/project-status/html", response_class=HTMLResponse)
    def dev_project_status_html() -> HTMLResponse:
        return HTMLResponse(render_project_status_html(build_project_status()))
