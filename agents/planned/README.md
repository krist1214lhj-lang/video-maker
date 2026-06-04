# planned/ — 에이전트 슬롯 (01~04, 06, 08)

아직 **별도 `.py` 구현이 없습니다.** 로직은 `main.py`·`audio_pipeline` 등에 있습니다.

| ID | 이름 | 비고 |
|----|------|------|
| 01 | topic_agent | 주제 생성 |
| 02 | story_agent | 스토리 대본 |
| 03 | character_agent | 캐릭터·레퍼런스 |
| 04 | format_agent | 출력 포맷 |
| 06 | music_agent | BGM (`audio_pipeline/bgm_generator.py`) |
| 08 | review_agent | 결과 검증 |

구현 시 `post_production/`과 동일하게 레거시 shim을 두고 `main.py` import를 단계적으로 옮깁니다.
