# Phase 2 layout (2026-06-03) — agents/docs/skills/backup only

**Checkpoint before changes:** `e019ff775ae5fe4ae46e722c37dbbf97a221bcfb`

## agents 이동 (본문)

| old path | new path |
|----------|----------|
| `agents/audio_subtitle_agent.py` (본문) | `agents/post_production/narration_subtitle.py` |
| `agents/export_agent.py` (본문) | `agents/post_production/production.py` |
| `agents/pipeline_director.py` (본문) | `agents/post_production/director.py` |

## agents 레거시 shim (경로 유지, main.py 무변경)

| path | 역할 |
|------|------|
| `agents/audio_subtitle_agent.py` | → `post_production.narration_subtitle` re-export |
| `agents/export_agent.py` | → `post_production.production` re-export |
| `agents/pipeline_director.py` | → `post_production.director` re-export |

## agents 신규

| path | 설명 |
|------|------|
| `agents/README.md` | 01~09 맵 |
| `agents/post_production/__init__.py` | 패키지 export |
| `agents/planned/README.md` | 미구현 슬롯 |
| `agents/planned/.gitkeep` | |

## docs 신규·갱신

| path | 설명 |
|------|------|
| `docs/STRUCTURE.md` | 목표 vs 현재 |
| `docs/agents/README.md` | 에이전트 문서 인덱스 |
| `docs/README.md` | 링크 추가 |

## skills 신규·갱신

| path | 설명 |
|------|------|
| `skills/safe_layout_cleanup.md` | Phase 2 제약 |
| `skills/README.md` | 링크 추가 |

## backup·archive

| old path | new path |
|----------|----------|
| `archive/cleanup_*` (있을 때) | `backup/archive_runs/cleanup_*` |
| (신규) | `backup/archive_runs/.gitkeep` |

루트 `archive/` 디렉터리는 **비워 두고 유지** (`main.py` ARCHIVE_DIR).

## 의도적으로 변경하지 않음

- `main.py`, `templates/index.html`
- `image_providers/`, `video_providers/`, `audio_pipeline/`, `video_editor/`
- `generated_*`, `video_jobs/`, `latest_videos/`, `projects/`
