# AGENT.md — 프로젝트 작업 기준 문서

이 문서는 **codex-project**에서 AI 에이전트·개발자가 항상 참고하는 단일 기준이다.  
코드 수정, 리팩토링, 버그 수정, 기능 추가 전에 이 문서를 먼저 읽는다.

---

## 1. 프로젝트 목표

- **주제 선택부터 최종 결과물 생성까지** 자동화하는 AI 콘텐츠 제작 시스템
- **출력물**: 숏츠, 동영상, 슬라이드, 카툰, 카드뉴스 등 (포맷은 `04_format_agent` 단계에서 결정)
- 사용자는 Topic → Story → Character → Format → Production → Review 흐름으로 진행하고, 각 단계는 **역할이 분리된 에이전트**가 담당한다.

---

## 2. 기본 작업 원칙

| 원칙 | 설명 |
|------|------|
| 한 번에 하나 | 한 PR·한 세션에서 **큰 기능 하나**만 수정한다. |
| 회귀 방지 | 기존 동작을 깨지 않는다. 변경 범위를 최소화한다. |
| 버튼 역할 분리 | UI 버튼마다 호출 API·에이전트가 **고정**이다. 임의로 체인에 끼워 넣지 않는다. |
| Audio+Subtitle ≠ Export | **Generate Audio + Subtitle**은 Final Export를 **절대** 호출하지 않는다. |
| Export 허용 경로만 | Final Export는 **Create Final Video** 또는 **Generate All**(Director 경유)에서만 실행한다. |
| 검증 필수 | 수정 후 **반드시** 검증 방법(수동·Network·로그)을 보고서에 적는다. |

### 버튼 ↔ 허용 API (절대 규칙)

| UI 버튼 | 허용 | 금지 |
|---------|------|------|
| Generate Video | 영상 생성 API만 | narration, subtitle, final-export |
| Generate Audio + Subtitle | `/agent/audio-subtitle/run` (또는 narration+subtitle만) | `/final-export`, `/agent/export/final`, `triggerProjectFinalExport` |
| Create Final Video | `/agent/export/final` 또는 `/final-export` (`source: manual_final_export`) | narration/subtitle 재생성 |
| Generate All | Director: video(클라이언트) → audio/subtitle → export | Audio+Subtitle 단독 경로에서 export 호출 |

### Final Export `source` 값

- `manual_final_export` — Create Final Video
- `generate_all` — Generate All의 export 단계  
- 그 외 값·빈 값 → 서버 **400** 거부

---

## 3. 에이전트 구조

### 3.1 목표 아키텍처 (번호 체계)

| ID | 에이전트 | 책임 |
|----|----------|------|
| 01 | `topic_agent` | 대주제·소주제 생성 |
| 02 | `story_agent` | 스토리 대본 3개 생성 |
| 03 | `character_agent` | 캐릭터 생성·고정 캐릭터 관리 |
| 04 | `format_agent` | 출력 방식 선택 (숏츠·슬라이드 등) |
| 05 | `narration_subtitle_agent` | 나레이션·자막 생성 |
| 06 | `music_agent` | 배경음악 생성 |
| 07 | `production_agent` | 최종 결과물(병합·렌더) 생성 |
| 08 | `review_agent` | 결과 검증·문제 피드백 |
| 09 | `director_agent` | 전체 오케스트레이션 |

자동 실행 체인은 **반드시 09 Director**를 통해서만 연결한다. 개별 에이전트가 다른 에이전트 API를 직접 호출하지 않는다.

### 3.2 현재 구현 (Phase 1 — 후반작업 분리)

구현 본문은 `agents/post_production/`에 있고, `main.py`는 **레거시 shim** 경로를 그대로 import한다.

| ID | 구현 (canonical) | `main.py` import (shim) | 역할 |
|----|------------------|-------------------------|------|
| 05 | `post_production/narration_subtitle.py` | `audio_subtitle_agent` | voice + subtitle만. **export import·호출 금지** |
| 07 | `post_production/production.py` | `export_agent` | timeline 검증 후 `final_export`만 |
| 09 | `post_production/director.py` | `pipeline_director` | audio만 / export만 / generate-all 순서 |

01~04, 06, 08은 [agents/planned/README.md](agents/planned/README.md) 슬롯만 있고 로직은 `main.py` 등에 남아 있다.

**HTTP API (에이전트)**

| 엔드포인트 | 용도 |
|------------|------|
| `POST /agent/audio-subtitle/run` | Audio + Subtitle 전용 |
| `POST /agent/export/final` | Create Final Video / Generate All export |
| `POST /agent/pipeline/generate-all` | audio_subtitle → export (Director) |

**레거시 API (유지, 내부 위임)**

| 엔드포인트 | 비고 |
|------------|------|
| `POST /generate-narration` | job 또는 selected_project |
| `POST /generate-subtitle` | 자막 단독 |
| `POST /generate-voice` | 프로젝트 voice |
| `POST /final-export` | `export_agent` 위임, `source` 필수 |

01~04, 06, 08은 아직 `main.py`·프론트에 분산되어 있으며, 점진적으로 `agents/` 하위 모듈로 이전한다.

---

## 4. 수정 금지 / 주의 규칙

1. **Audio / Subtitle / Final Export를 하나의 공용 후반작업 함수에 섞지 않는다.**  
   예: `runPostProductionFromStoryline`에 export를 조건부로 넣는 패턴은 신규 코드에서 금지. Director 또는 전용 버튼 핸들러만 사용.

2. **자동 체인은 Director만 연결한다.**  
   Generate All = `pipeline_director.run_generate_all_pipeline` (또는 동등한 명시적 순서).

3. **승인 없는 외부 의존성 금지**  
   플러그인·npm/pip 패키지·외부 API·MCP 추가는 사용자 승인 전 **설치·연동하지 않는다.**

4. **새 도구가 필요할 때**  
   다음을 문서화한 뒤 승인을 받는다: **이유**, **장점**, **단점**, **적용 범위**(어떤 에이전트·파일만).

5. **프론트 fetch 가드**  
   `templates/index.html`의 Final Export guard는 Audio+Subtitle lock 중 `/final-export`, `/agent/export/final` POST를 차단한다. 이 가드를 우회하는 코드를 넣지 않는다.

6. **짧은 대사형 (Audio + Subtitle)**  
   `force_short_dialogue: true`일 때 컷 순서별 5줄 대사를 우선한다 (storyline 설명문 그대로 사용 금지).

---

## 5. 스킬·문서 구조 (Phase 1)

| 영역 | 경로 | 설명 |
|------|------|------|
| 작업 기준 | `AGENT.md` | 이 문서 |
| 구조 로드맵 | [docs/STRUCTURE.md](docs/STRUCTURE.md) | AUTO_VIDEO_MAKER 목표 vs 현재 (URL·outputs 미변경) |
| 에이전트 맵 | [agents/README.md](agents/README.md) | 01~09 파일 배치 |
| 스킬 인덱스 | [skills/README.md](skills/README.md) | 실행 가이드 목록 |
| 문서 인덱스 | [docs/README.md](docs/README.md) | Hermes 등 보조 문서 |
| 백업 | [backup/README.md](backup/README.md) | temp·archive_runs·스냅샷 보관 |

### skills/

| 스킬 | 경로 |
|------|------|
| 서버 기동 | [skills/start_server.md](skills/start_server.md) |
| ngrok | [skills/start_ngrok.md](skills/start_ngrok.md) |
| 프로젝트 복구 | [skills/restore_project.md](skills/restore_project.md) |
| 파이프라인 검증 | [skills/test_pipeline.md](skills/test_pipeline.md) |
| Final Export 디버그 | [skills/debug_final_export.md](skills/debug_final_export.md) |
| Generate Video 디버그 | [skills/debug_generate_video.md](skills/debug_generate_video.md) |
| 새 에이전트 추가 | [skills/create_agent.md](skills/create_agent.md) |
| 스토리→영상 흐름 | [skills/create_story_video.md](skills/create_story_video.md) |
| 안전 폴더 정리 | [skills/safe_layout_cleanup.md](skills/safe_layout_cleanup.md) |

에이전트는 작업 전 **skills/README.md** + 이 `AGENT.md`를 참고한다.

---

## 6. 현재 최우선 리팩토링 목표

**기존 후반작업 파이프라인을 폐기**하고, Audio+Subtitle과 Final Export를 **완전 분리**한다.

### 목표 상태

| 시나리오 | 흐름 | FINAL EXPORT |
|----------|------|----------------|
| Generate Audio + Subtitle | video 확인 → narration → subtitle | **0/1 유지** |
| Create Final Video | video/audio/subtitle 확인 → final export | **1/1** |
| Generate All | video → audio/subtitle → final export (Director) | **1/1** |

### 검증 체크리스트 (Network 탭)

- **A.** Audio + Subtitle: `/final-export`, `/agent/export/final` 요청 **없음**
- **B.** Create Final Video: export API **1회**
- **C.** Generate All: video 후 Director 파이프라인, export **1회**
- **D.** 짧은 대사 5줄이 narration/subtitle에 반영됨

### 완료 정의

- [ ] `audio_subtitle_agent`가 `export_agent`를 import하지 않음
- [ ] Audio+Subtitle 버튼이 agent audio-subtitle API만 호출
- [ ] Export 버튼만 export agent API 호출
- [ ] Generate All이 `pipeline_director`만으로 후반+export 연결
- [x] 05·07·09 구현이 `agents/post_production/`에 배치 (shim으로 `main.py` 무변경)
- [ ] 01~04, 06, 08이 `agents/planned/`에서 실제 모듈로 이전 (Phase 3+)

---

## 7. 개발 환경 규칙

| 항목 | 값 |
|------|-----|
| 작업 폴더 | `/home/hyunjun/codex-project` |
| 서버 포트 | **8011 고정** (다른 포트로 fallback 금지) |
| 서버 실행 | **SERVER** 터미널 — `python start_server.py` 또는 `uvicorn main:app --host 127.0.0.1 --port 8011 --reload` |
| ngrok | **NGROK** 터미널 — [skills/start_ngrok.md](skills/start_ngrok.md), `scripts/restart-ngrok.sh` |
| 코드 수정 | **Cursor** |
| 헬스 체크 | `curl http://127.0.0.1:8011/health` |

WSL에서 Windows Cursor로 연 경우에도 경로·포트 규칙은 동일하다.

---

## 8. 완료 후 보고 형식

작업이 끝나면 **반드시** 아래 형식으로 보고한다.

```markdown
## 작업 보고

### 변경 파일
- (경로 목록)

### 새로 만든 파일
- (경로 목록)

### 수정한 함수
- `함수명` — (파일) — 한 줄 요약

### 왜 수정했는지
- (목표·버그·규칙 중 무엇을 만족하는지)

### 테스트 방법
1. ...
2. Network / 로그에서 확인할 항목

### 남은 문제
- (없으면 "없음")
```

---

## 9. 폴더 레이아웃 (Phase 1 정리 후)

```text
codex-project/
├── AGENT.md, README.md, main.py, start_server.py
├── agent.md                 # stub → skills/debug_generate_video.md
├── agents/
│   ├── post_production/     # 05, 07, 09 구현
│   ├── planned/             # 01~04, 06, 08 슬롯 (문서)
│   └── *_agent.py           # main.py용 레거시 shim
├── skills/, docs/           # STRUCTURE.md, safe_layout_cleanup.md 포함
├── backup/
│   ├── temp_files/
│   ├── old_versions/archive/
│   ├── archive_runs/        # 루트 archive/cleanup_* 보관
│   └── snapshots/
├── archive/                 # main.py ARCHIVE_DIR (런타임, 비어 있어도 됨)
├── templates/, static/      # 변경 없음
├── image_providers/, video_providers/, audio_pipeline/, video_editor/
├── projects/, reference_characters/
└── generated_*, video_jobs/, latest_videos/   # 루트 유지 (outputs 통합은 미승인)
```

상세 목표 구조: [docs/STRUCTURE.md](docs/STRUCTURE.md)

## 참고 파일

| 파일 | 내용 |
|------|------|
| `main.py` | FastAPI 라우트·프로젝트 파이프라인 |
| `templates/index.html` | 버튼·fetch guard·후반작업 UI |
| `agents/post_production/*.py` | 05·07·09 구현 |
| `agents/*_agent.py` | `main.py`용 shim |
| `README.md` | 설치·환경변수·엔드포인트 |
| `docs/STRUCTURE.md` | AUTO_VIDEO_MAKER 로드맵 |
| `skills/debug_generate_video.md` | Generate Video 디버그 (구 `agent.md` 본문) |
| `backup/snapshots/PHASE0-2026-06-03-inventory.md` | Phase 0 이동 기록 |
| `backup/snapshots/PHASE2-2026-06-03-layout.md` | Phase 1 agents/docs 정리 기록 |
| `.cursor/rules/resume-session.mdc` | 세션 재개 메모 |

문서와 코드가 어긋나면 **코드 동작을 우선** 확인한 뒤, 이 `AGENT.md`를 갱신한다.
