# Session Resume — 2026-06-02

> `.cursor/session-resume.md` 복사본 (원본 유지)

**상태:** in-progress  
**브랜치:** main  
**프로젝트:** `/home/hyunjun/codex-project` (WSL)  
**테스트 프로젝트:** `test-01` (선택 컷: 1, 3, 5)

## Working on

**Generate Audio + Subtitle** 버튼만 눌렀을 때는 Audio/Subtitle만 생성하고, **Final Export는 사용자가 `Create Final Video`를 직접 누르거나 `Generate All`을 누를 때만** 실행되어야 함.

## Decisions Made

- **서버 export 로직 변경 금지** — Mock/Replicate/ngrok/`final_timeline`/`main.py` export 로직 건드리지 않음.
- 수정 범위는 주로 **`templates/index.html`** (프론트).
- Opt-in 모델 도입:
  - `finalExportFetchPermitted` — Create Final Video / Generate All 클릭 시에만 true
  - `finalExportBlockedAfterAudioSubtitleOnly` — Audio+Subtitle 완료 후 export 차단
  - `grantFinalExportFetchPermission()` / `revokeFinalExportFetchPermission()`
- fetch 3중 방어: `window.fetch` 가드 + `fetchJsonPost` + `runFinalExport`/`assertFinalExportSourceAllowed`
- Audio+Subtitle 후 이전 `final_export.mp4` UI 착시 방지: `invalidateFinalExportAfterAudioSubtitleOnly()`, merge 시 stale final video 무시
- `canFinalExport: true`는 **버튼 활성화 조건**이지 export 자동 실행이 아님 (4초 dashboard 폴링으로 로그 반복)
- 로그: `[FINAL-EXPORT-READINESS]`, 상태 변경 시에만 telemetry 출력

## Remaining Work (우선순위)

1. **Ctrl+Shift+R** 강력 새로고침 후 검증 (2026-06-03 수정 반영):
   - A) Generate Audio + Subtitle → `POST /final-export` 없음, FINAL EXPORT **0/1**
   - B) Create Final Video → `source: manual_final_export`, FINAL EXPORT **1/1**
   - C) Generate All → `source: generate_all`, FINAL EXPORT **1/1**
2. B에서 여전히 400이면 서버 `detail.error` (timeline/video/audio/subtitle 누락) 확인 — source 차단이 아님
3. 커밋은 사용자 요청 시만

## Key Files

| 파일 | 역할 |
|------|------|
| `templates/index.html` | `runAudioSubtitlePipeline`, `runFinalExport`, fetch guard, UI invalidation |
| `main.py` | `/final-export` (수정 금지, 디버그만) |

## Server / Dev

```bash
cd /home/hyunjun/codex-project
python start_server.py   # port 8011
```

## Notes

- 사용자는 **한국어**로 응답 선호.
- git: main 브랜치, `templates/index.html` 등 다수 미커밋 변경 있음 (커밋은 사용자 요청 시만).
