# 스킬: 후반작업 파이프라인 검증

## 목적

버튼별 역할 분리가 **Network·상태 카운터** 기준으로 맞는지 확인한다. (`AGENT.md` 6절)

## 준비

1. [start_server.md](start_server.md)로 서버 기동
2. 브라우저 `http://127.0.0.1:8011` 접속
3. DevTools → **Network** 탭, Preserve log 켜기
4. 프로젝트 선택 + motion 컷 선택 + **VIDEO 1/1** 상태 확보

## A. Generate Audio + Subtitle

1. **Generate Audio + Subtitle** 클릭
2. 확인:
   - `POST /agent/audio-subtitle/run` **1회** (또는 narration+subtitle만, export 없음)
   - `/final-export`, `/agent/export/final` **없음**
3. 대시보드: VIDEO 1/1, AUDIO 1/1, SUBTITLE 1/1, **FINAL EXPORT 0/1**
4. 짧은 대사 5줄이 적용됐는지 UI 미리보기·`script.json` 확인

## B. Create Final Video

1. A 완료 상태에서 **Create Final Video** 클릭
2. 확인:
   - `POST /agent/export/final` 또는 `/final-export` **1회**
   - `source: manual_final_export`
3. FINAL EXPORT **1/1**

## C. Generate All (선택 컷 후반)

1. 새로고침 후 영상·스토리라인 준비
2. **Generate All** (선택 컷 일괄 후반) 실행
3. 확인:
   - 영상 생성 후 `POST /agent/pipeline/generate-all` (또는 video → agent pipeline)
   - export **1회**, `source: generate_all`
4. FINAL EXPORT **1/1**

## 서버 로그 키워드

- `[audio-subtitle-agent]` — Audio+Subtitle만
- `[export-agent]` — Final export만
- `[FINAL-EXPORT-BLOCKED]` — 프론트 가드 차단 (Audio 중 export 시도 시)

## 실패 시

- export가 Audio+Subtitle 직후 자동 호출 → `templates/index.html`의 `runAudioSubtitleOnlyForSelectedCuts`가 export 함수를 호출하는지 grep
- 400 on export → timeline 미완료; `skills/debug_final_export.md` 참고
