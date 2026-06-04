"""Audio + subtitle generation only. Must never invoke final export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AudioSubtitleRunInput:
    project_slug: str
    project_dir: Path
    selected_cuts: list[int] | None = None
    storyboard_id: str | None = None
    force_short_dialogue: bool = True


@dataclass
class AudioSubtitleRunResult:
    success: bool
    project: str
    selected_cuts: list[int]
    script: dict[str, Any]
    narration: dict[str, Any]
    subtitle: dict[str, Any]
    audio_count: int
    cue_count: int
    dialogue_source: str


def _clear_final_export_state(project_dir: Path, script: dict[str, Any]) -> None:
    from main import ensure_current_run, script_export_cut_order, write_current_run

    cut_order = script_export_cut_order(script)
    run = ensure_current_run(
        project_dir,
        cut_order,
        target_duration=script.get("duration"),
        reset_if_changed=False,
    )
    run["audio_done"] = []
    run["subtitle_done"] = []
    run["final_export_path"] = ""
    run["final_export_url"] = ""
    run["final_export_selected_cuts"] = []
    write_current_run(project_dir, run)


def run_audio_subtitle(input_data: AudioSubtitleRunInput) -> AudioSubtitleRunResult:
    """
    Run narration (voice) then subtitles for selected cuts.
    Intentionally does not import or call export_agent / generate_project_final_export.
    """
    from main import (
        generate_project_subtitle,
        generate_project_voice,
        reconcile_script_export_cuts,
        script_export_cut_order,
        write_short_dialogue_script_for_selected_cuts,
    )

    project_slug = input_data.project_slug
    project_dir = input_data.project_dir
    selected_cuts = input_data.selected_cuts
    storyboard_id = input_data.storyboard_id

    if input_data.force_short_dialogue:
        script = write_short_dialogue_script_for_selected_cuts(
            project_slug,
            project_dir,
            selected_cuts=selected_cuts,
        )
        text_map = None
        dialogue_source = "short_dialogue"
    else:
        script = reconcile_script_export_cuts(project_dir, selected_cuts=selected_cuts)
        _clear_final_export_state(project_dir, script)
        text_map = None
        dialogue_source = str(script.get("source") or "storyline")

    print(
        "[audio-subtitle-agent] start",
        {
            "project": project_slug,
            "selected_cuts": selected_cuts,
            "dialogue_source": dialogue_source,
        },
    )

    narration_payload = generate_project_voice(
        project_slug,
        project_dir,
        selected_cuts=selected_cuts,
        text_map=text_map,
        storyboard_id=storyboard_id,
    )

    subtitle_payload = generate_project_subtitle(
        project_slug,
        project_dir,
        selected_cuts=selected_cuts,
        text_map=text_map,
        storyboard_id=storyboard_id,
    )

    script = reconcile_script_export_cuts(project_dir, selected_cuts=selected_cuts)
    cut_order = script_export_cut_order(script)

    print(
        "[audio-subtitle-agent] done",
        {
            "project": project_slug,
            "audio_count": narration_payload.get("audio_count"),
            "cue_count": subtitle_payload.get("cue_count"),
        },
    )

    return AudioSubtitleRunResult(
        success=True,
        project=project_slug,
        selected_cuts=cut_order,
        script=script,
        narration=narration_payload,
        subtitle=subtitle_payload,
        audio_count=int(narration_payload.get("audio_count") or 0),
        cue_count=int(subtitle_payload.get("cue_count") or 0),
        dialogue_source=dialogue_source,
    )
