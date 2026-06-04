# 프로젝트 구조 (AUTO_VIDEO_MAKER 방향)

문서 전용 로드맵입니다. **URL·`main.py`·outputs 경로는 아직 변경하지 않습니다.**

## 현재 (안정화 단계)

```text
codex-project/
├── main.py, start_server.py      # 실행 로직 (분해 금지)
├── agents/
│   ├── post_production/          # 05, 07, 09 구현
│   ├── planned/                  # 01~04, 06, 08 슬롯
│   └── *_agent.py                # main.py용 레거시 shim
├── image_providers/, video_providers/, audio_pipeline/, video_editor/
├── templates/, static/
├── projects/, reference_characters/
├── generated_*, video_jobs/, latest_videos/   # 루트 유지
├── archive/                      # main.py ARCHIVE_DIR (런타임)
├── skills/, docs/, backup/, config/, scripts/
```

## 목표 (승인 후 단계)

| 영역 | 목표 | 현재 |
|------|------|------|
| providers | `providers/image|video|audio/` | 루트 `*_providers/`, `audio_pipeline/` |
| pipelines | `pipelines/export/` | `video_editor/` |
| outputs | `outputs/...` | 루트 `generated_*` 등 |
| services | `services/*.py` | `main.py` 내 함수 |

## 이번 단계에서 하지 않는 것

- `main.py` 분해
- `services/`, `pipelines/*.py` 신규
- URL·mount 경로 변경
- `generated_outputs` 이동
- `templates/index.html` 구조 변경

## 참고

- [agents/README.md](../agents/README.md)
- [AGENT.md](../AGENT.md)
- [backup/snapshots/PHASE2-2026-06-03-layout.md](../backup/snapshots/PHASE2-2026-06-03-layout.md)
