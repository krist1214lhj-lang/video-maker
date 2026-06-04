# 스킬: Final Export 디버그

## 목적

Final Export 400/403, 자동 호출, 타임라인 미준비 문제를 진단한다.

## 증상별 체크

### 1. Audio+Subtitle 직후 export 400

**원인 후보:** Generate Audio + Subtitle 경로에서 export가 잘못 호출됨.

**확인:**

- Network에 `/final-export` 또는 `/agent/export/final` 있는지
- 콘솔 `[FINAL-EXPORT-BLOCKED]` / `[ASSEMBLE-FINAL-VIDEO-BLOCKED-BY-FETCH-GUARD]`

**수정 방향:** export 호출을 Create Final Video / Generate All(Director)로만 제한 (`AGENT.md` 2절).

### 2. Create Final Video 400 — timeline not ready

**원인:** video/audio/subtitle 파일·메타가 export 기준에 미달.

**확인:**

- 응답 `detail.timeline_status` 또는 `missing_details`
- 프로젝트 `current_run.json`, `script.json`, 컷별 mp4·audio·subtitle 경로

**수정 방향:** `agents/export_agent.py`의 `_validate_timeline_ready` 메시지에 맞춰 누락 컷 재생성.

### 3. 403 blocked / invalid source

**원인:** `source`가 `manual_final_export` 또는 `generate_all`이 아님, 또는 fetch permission nonce 없음.

**확인:**

- 요청 body의 `source`, `export_permission_nonce`
- Audio+Subtitle lock 활성 여부

### 4. source 빈 값

서버는 빈 `source`를 **400**으로 거부한다. 프론트에서 `grantFinalExportFetchPermission` 후 export 호출하는지 확인.

## 유용한 grep

```bash
rg "final-export|export/final|triggerProjectFinalExport" templates/index.html
rg "run_final_export|export_agent" agents/ main.py
```

## 로그

서버:

- `[final-export] START` / `DONE` / `FAIL`
- `[export-agent] start` / `done`

## 수동 API 테스트

```bash
curl -s -X POST http://127.0.0.1:8011/agent/export/final \
  -H "Content-Type: application/json" \
  -d '{"selected_project":"YOUR_SLUG","source":"manual_final_export"}'
```

`source` 없이 `/final-export` 호출 시 400이 정상이다.
