# Phase 0~1 인벤토리 (2026-06-03)

## 이동 완료 (루트 → backup)

| old path | new path |
|----------|----------|
| `all_commands_output.txt` | `backup/temp_files/all_commands_output.txt` |
| `wsl_ls.txt` | `backup/temp_files/wsl_ls.txt` |
| `wsl_out.txt` | `backup/temp_files/wsl_out.txt` |
| `run_user_commands.sh` | `backup/temp_files/run_user_commands.sh` |
| `nul` | `backup/temp_files/nul_windows_artifact` |
| `archive/` | `backup/old_versions/archive/` |

## 이동 완료 (문서)

| old path | new path |
|----------|----------|
| `agent.md` (본문) | `skills/debug_generate_video.md` |
| `agent.md` (루트) | stub → `skills/debug_generate_video.md` 링크 |

## 복사 (원본 유지)

| source | copy |
|--------|------|
| `.agents/hermes.md` | `docs/agents/hermes.md` |
| `.cursor/session-resume.md` | `backup/snapshots/session-resume-2026-06.md` |

## Phase 0~1에서 수정하지 않은 항목

- `main.py`, `templates/index.html`, `agents/*.py`
- `providers/`, `static/`, `outputs/` (generated_*, latest_videos 등)
