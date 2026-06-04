# agents/

AUTO_VIDEO_MAKER 번호 체계(01~09)를 기준으로 에이전트 모듈을 배치합니다.  
**실행 로직은 `main.py`에 남아 있으며**, 이 폴더는 후반작업(05·07·09)만 분리된 상태입니다.

## 구현됨 (`post_production/`)

| ID | 파일 | 레거시 import (유지) | 역할 |
|----|------|----------------------|------|
| 05 | `post_production/narration_subtitle.py` | `audio_subtitle_agent` | 음성·자막 |
| 07 | `post_production/production.py` | `export_agent` | Final export |
| 09 | `post_production/director.py` | `pipeline_director` | 후반 오케스트레이션 |

`main.py`는 **레거시 경로**(`agents.export_agent` 등)를 그대로 사용합니다. 동작 변경 없음.

## 예정 (`planned/`)

| ID | 계획 모듈 | 현재 위치 |
|----|-----------|-----------|
| 01 topic | `planned/01_topic_agent.py` | `main.py` |
| 02 story | `planned/02_story_agent.py` | `main.py` |
| 03 character | `planned/03_character_agent.py` | `main.py` |
| 04 format | `planned/04_format_agent.py` | `main.py` |
| 06 music | `planned/06_music_agent.py` | `audio_pipeline` |
| 08 review | `planned/08_review_agent.py` | 미구현 |

Phase 3 이후 승인 시 `main.py`에서 점진 이전합니다.

## 규칙

- 05는 07을 import·호출하지 않는다.
- 07은 narration/subtitle 생성기를 호출하지 않는다.
- 자동 체인은 09 Director만 연결한다.

자세한 버튼·API 규칙: [AGENT.md](../AGENT.md)
