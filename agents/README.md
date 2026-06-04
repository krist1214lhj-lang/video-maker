# agents/

에이전트 모듈 루트. 자동 체인은 **Director(09)** 만 연결 (`AGENT.md` §3).

## Phase 3A-3 — 전체 Mock 파이프라인 (main/API/UI 미연결)

| ID | 모듈 | 역할 |
|----|------|------|
| 01 | [01_topic_agent.py](01_topic_agent.py) | 대주제 → 소주제 |
| 02 | [02_story_agent.py](02_story_agent.py) | 스토리 3톤 |
| 03 | [03_character_agent.py](03_character_agent.py) | `reference_character` → profile / prompt / constraints |
| 04 | [04_format_agent.py](04_format_agent.py) | `format_plan`, cut count, aspect ratio |
| 05 | [05_narration_subtitle_agent.py](05_narration_subtitle_agent.py) | narration / subtitle scripts, voice style |
| 06 | [06_music_agent.py](06_music_agent.py) | music style, BPM, prompt |
| 07 | [07_production_agent.py](07_production_agent.py) | production_plan, render_plan |
| 08 | [08_review_agent.py](08_review_agent.py) | review_report, retry_target_agent |
| 09 | [09_director_agent.py](09_director_agent.py) | `run_full_pipeline()` 01→08 |

```text
09 Director → 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08
```

[docs/agents/DIRECTOR_PIPELINE.md](../docs/agents/DIRECTOR_PIPELINE.md)

## Phase 1 — 후반작업 (main.py shim)

| ID | 구현 | shim |
|----|------|------|
| 05 | [post_production/narration_subtitle.py](post_production/narration_subtitle.py) | `audio_subtitle_agent.py` |
| 07 | [post_production/production.py](post_production/production.py) | `export_agent.py` |
| 09 | [post_production/director.py](post_production/director.py) | `pipeline_director.py` |

Mock `05_*` / `07_*` 와 **파일·역할이 겹치지만 import 경로가 다름** — Phase 3B에서 통합 예정.

## 예정

[planned/README.md](planned/README.md)
