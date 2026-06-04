# agents/

에이전트 모듈 루트. 자동 체인은 **Director만** 연결한다 (`AGENT.md` §3).

## Phase 3A-2 — 기획 (main/API/UI 미연결)

| ID | 모듈 | 역할 |
|----|------|------|
| 01 | [01_topic_agent.py](01_topic_agent.py) | 대주제 → 소주제 |
| 02 | [02_story_agent.py](02_story_agent.py) | 소주제당 스토리 3톤 |
| 03 | [03_character_agent.py](03_character_agent.py) | `reference_character` → `character_profile`, `character_prompt`, `visual_constraints` |
| 04 | [04_format_agent.py](04_format_agent.py) | `format_plan`, `recommended_cut_count`, `aspect_ratio` |
| 09 | [09_director_agent.py](09_director_agent.py) | 기획 오케스트레이션 01→04 |

호출 다이어그램: [docs/agents/DIRECTOR_PIPELINE.md](../docs/agents/DIRECTOR_PIPELINE.md)

```text
09 Director → 01 Topic → 02 Story → 03 Character → 04 Format
```

## Phase 1 — 후반작업 (main.py shim 연결됨)

| ID | 구현 | shim |
|----|------|------|
| 05 | [post_production/narration_subtitle.py](post_production/narration_subtitle.py) | `audio_subtitle_agent.py` |
| 07 | [post_production/production.py](post_production/production.py) | `export_agent.py` |
| 09 | [post_production/director.py](post_production/director.py) | `pipeline_director.py` |

## 예정

[planned/README.md](planned/README.md) — 06 music, 08 review 등
