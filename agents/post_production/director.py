"""Agent 09 — post-production orchestration (audio/subtitle → export)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.post_production.narration_subtitle import AudioSubtitleRunInput, run_audio_subtitle
from agents.post_production.production import ExportRunInput, run_final_export


@dataclass
class GenerateAllPipelineInput:
    project_slug: str
    project_dir: Path
    selected_cuts: list[int] | None = None
    storyboard_id: str | None = None
    force_short_dialogue: bool = False
    run_final_export: bool = True
    export_source: str = "generate_all"


@dataclass
class GenerateAllPipelineResult:
    success: bool
    project: str
    selected_cuts: list[int]
    audio_subtitle: dict[str, Any]
    final_export: dict[str, Any] | None
    steps: list[str]


def run_audio_subtitle_only(
    project_slug: str,
    project_dir: Path,
    *,
    selected_cuts: list[int] | None = None,
    storyboard_id: str | None = None,
    force_short_dialogue: bool = True,
) -> dict[str, Any]:
    """Generate Audio + Subtitle button path — never runs export."""
    result = run_audio_subtitle(
        AudioSubtitleRunInput(
            project_slug=project_slug,
            project_dir=project_dir,
            selected_cuts=selected_cuts,
            storyboard_id=storyboard_id,
            force_short_dialogue=force_short_dialogue,
        )
    )
    return {
        "success": result.success,
        "project": result.project,
        "selected_cuts": result.selected_cuts,
        "dialogue_source": result.dialogue_source,
        "audio_count": result.audio_count,
        "cue_count": result.cue_count,
        "script": result.script,
        "narration": result.narration,
        "subtitle": result.subtitle,
        "steps": ["audio_subtitle"],
    }


def run_export_only(
    project_slug: str,
    project_dir: Path,
    *,
    selected_cuts: list[int] | None = None,
    source: str = "manual_final_export",
) -> dict[str, Any]:
    """Create Final Video button path — export agent only."""
    result = run_final_export(
        ExportRunInput(
            project_slug=project_slug,
            project_dir=project_dir,
            selected_cuts=selected_cuts,
            source=source,
        )
    )
    return {
        "success": result.success,
        "project": result.project,
        "selected_cuts": result.selected_cuts,
        "source": result.source,
        "timeline_status": result.timeline_status,
        "export": result.export,
        "steps": ["export"],
    }


def run_generate_all_pipeline(input_data: GenerateAllPipelineInput) -> GenerateAllPipelineResult:
    """
    Post-production leg for Generate All: audio_subtitle_agent → export_agent.
    Video generation remains client-side in phase 1.
    """
    steps: list[str] = ["video_skipped_client"]

    audio_result = run_audio_subtitle(
        AudioSubtitleRunInput(
            project_slug=input_data.project_slug,
            project_dir=input_data.project_dir,
            selected_cuts=input_data.selected_cuts,
            storyboard_id=input_data.storyboard_id,
            force_short_dialogue=input_data.force_short_dialogue,
        )
    )
    steps.append("audio_subtitle")

    export_payload: dict[str, Any] | None = None
    if input_data.run_final_export:
        export_result = run_final_export(
            ExportRunInput(
                project_slug=input_data.project_slug,
                project_dir=input_data.project_dir,
                selected_cuts=input_data.selected_cuts,
                source=input_data.export_source,
            )
        )
        export_payload = {
            "source": export_result.source,
            "timeline_status": export_result.timeline_status,
            "export": export_result.export,
        }
        steps.append("export")

    return GenerateAllPipelineResult(
        success=True,
        project=input_data.project_slug,
        selected_cuts=audio_result.selected_cuts,
        audio_subtitle={
            "dialogue_source": audio_result.dialogue_source,
            "audio_count": audio_result.audio_count,
            "cue_count": audio_result.cue_count,
            "narration": audio_result.narration,
            "subtitle": audio_result.subtitle,
        },
        final_export=export_payload,
        steps=steps,
    )
