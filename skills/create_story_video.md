# 스킬: 스토리 → 영상 제작 흐름

## 목적

Topic부터 선택 컷 최종 영상까지 **에이전트·버튼 순서**를 이해하고 실행한다.

## 전체 흐름 (목표 아키텍처)

```text
01 topic_agent      → 주제·소주제
02 story_agent      → 스토리 3안
03 character_agent  → 캐릭터·레퍼런스
04 format_agent     → 출력 포맷
     ↓
[ Generate Video ]  → 컷별 영상 (클라이언트/영상 API)
     ↓
05 narration_subtitle_agent  → Audio + Subtitle
06 music_agent      → BGM (선택, Generate All 등)
07 production_agent → Final Export
08 review_agent     → 품질 검증
09 director_agent   → Generate All 등 명시적 오케스트레이션
```

## 현재 UI에서의 실무 순서

1. **Topic / Scenario / Storyline** — 스토리보드·스토리라인 생성
2. **Generate Video** — motion 컷 영상 (`/generate-video-clip` 등)
3. **Generate Audio + Subtitle** — `POST /agent/audio-subtitle/run`  
   - video 완료 확인 → narration → subtitle  
   - FINAL EXPORT **0/1** 유지
4. **Create Final Video** — `POST /agent/export/final` (`manual_final_export`)
5. **Generate All** — 영상 배치 후 Director:  
   `POST /agent/pipeline/generate-all` (audio/subtitle → export)

## 짧은 대사형 (테스트·데모)

Audio + Subtitle 시 `force_short_dialogue: true` 기본:

1. "어? 오늘은 또 무슨 일이 있었지?"
2. "흠... 뭔가 수상한데?"
3. "잠깐만, 이건 예상 못 했어."
4. "그래도 일단 지켜보자."
5. "결국 또 이렇게 되는구나."

Storyline 설명문을 그대로 쓰지 않는다.

## 관련 파일

| 단계 | 코드 |
|------|------|
| 스토리·스크립트 | `main.py` — storyline, `/generate-script` |
| 영상 | `video_providers/`, `/generate-video-clip` |
| 음성·자막 | `agents/audio_subtitle_agent.py`, `audio_pipeline/` |
| 최종 병합 | `agents/export_agent.py`, `video_editor/` |
| UI | `templates/index.html` |

## 검증

[test_pipeline.md](test_pipeline.md) 시나리오 A~C 실행.

## 주의

- 후반작업을 한 함수에 묶어서 export까지 돌리지 않는다 (`AGENT.md` 4절).
- Generate All만 Director가 export까지 호출한다.
