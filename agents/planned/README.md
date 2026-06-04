# planned/ — 에이전트 슬롯 (06, 08)

| ID | 이름 | 비고 |
|----|------|------|
| 01 | topic_agent | **Phase 3A-2** → [../01_topic_agent.py](../01_topic_agent.py) (main 미연결) |
| 02 | story_agent | **Phase 3A-2** → [../02_story_agent.py](../02_story_agent.py) (main 미연결) |
| 03 | character_agent | **Phase 3A-2** → [../03_character_agent.py](../03_character_agent.py) (main 미연결) |
| 04 | format_agent | **Phase 3A-2** → [../04_format_agent.py](../04_format_agent.py) (main 미연결) |
| 09 | director (기획) | **Phase 3A-2** → [../09_director_agent.py](../09_director_agent.py) — 01→04, [DIRECTOR_PIPELINE.md](../../docs/agents/DIRECTOR_PIPELINE.md) |
| 09 | director (후반) | 구현 → [../post_production/director.py](../post_production/director.py) |
| 06 | music_agent | BGM (`audio_pipeline/bgm_generator.py`) |
| 08 | review_agent | 결과 검증 |

구현 시 `post_production/`과 동일하게 레거시 shim을 두고 `main.py` import를 단계적으로 옮깁니다.
