"""Final export only. Must not generate audio or subtitles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_EXPORT_SOURCES = frozenset({"manual_final_export", "generate_all"})


@dataclass
class ExportRunInput:
    project_slug: str
    project_dir: Path
    selected_cuts: list[int] | None = None
    source: str = "manual_final_export"


@dataclass
class ExportRunResult:
    success: bool
    source: str
    project: str
    selected_cuts: list[int]
    timeline_status: dict[str, Any]
    export: dict[str, Any]


def _validate_export_source(source: str) -> str:
    normalized = str(source or "").strip()
    if normalized not in ALLOWED_EXPORT_SOURCES:
        raise ValueError(
            "Invalid final export source. Use manual_final_export or generate_all."
        )
    return normalized


def _validate_timeline_ready(
    project_slug: str,
    project_dir: Path,
    selected_cuts: list[int] | None,
) -> dict[str, Any]:
    from fastapi import HTTPException

    from main import build_hyperframe_timeline_status

    status = build_hyperframe_timeline_status(
        project_slug,
        project_dir,
        selected_cuts=selected_cuts,
    )
    if not status.get("can_final_export"):
        missing = status.get("missing_details") or []
        lines = [
            f"Cut {item.get('cut_number')}: {item.get('reason')}"
            for item in missing
            if item.get("cut_number")
        ]
        message = "\n".join(lines) if lines else "Video, audio, and subtitle must be ready before final export."
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": message,
                "timeline_status": status,
            },
        )
    return status


def run_final_export(input_data: ExportRunInput) -> ExportRunResult:
    """Merge final video for selected cuts. Does not touch narration/subtitle generators."""
    from main import generate_project_final_export, script_export_cut_order

    source = _validate_export_source(input_data.source)
    project_slug = input_data.project_slug
    project_dir = input_data.project_dir
    selected_cuts = input_data.selected_cuts

    timeline_status = _validate_timeline_ready(project_slug, project_dir, selected_cuts)

    print(
        "[export-agent] start",
        {
            "project": project_slug,
            "source": source,
            "selected_cuts": selected_cuts,
        },
    )

    export_payload = generate_project_final_export(
        project_slug,
        project_dir,
        selected_cuts=selected_cuts,
    )
    cut_order = script_export_cut_order(export_payload.get("script") or {}) or timeline_status.get(
        "selected_cut_numbers"
    ) or []

    print(
        "[export-agent] done",
        {
            "project": project_slug,
            "output": export_payload.get("output_file"),
        },
    )

    return ExportRunResult(
        success=True,
        source=source,
        project=project_slug,
        selected_cuts=cut_order if isinstance(cut_order, list) else list(cut_order or []),
        timeline_status=timeline_status,
        export=export_payload,
    )
