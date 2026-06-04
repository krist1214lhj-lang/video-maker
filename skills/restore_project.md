# 스킬: 프로젝트 복구

## 목적

`projects/{slug}/` 데이터가 깨졌거나 UI에서 프로젝트가 보이지 않을 때 **최소 복구** 절차입니다.

## 확인할 파일 (프로젝트당)

| 파일 | 역할 |
|------|------|
| `project.json` | slug·메타데이터 |
| `storyboard.json` | 컷·이미지 |
| `script.json` | 나레이션·자막 텍스트 |
| `current_run.json` | VIDEO/AUDIO/SUBTITLE/EXPORT 진행 상태 |

## 기본 프로젝트 slug

- 기본 활성: `bposik_rainy_home` (`main.py` `DEFAULT_PROJECT_SLUG`)
- 테스트: `test-01`, `test_02`, `test_03`

## 복구 절차

1. 서버 중지 없이도 가능 — `projects/{slug}/project.json` 존재 여부 확인
2. `storyboard.json` 없으면 UI에서 Storyline/Storyboard 재생성
3. `current_run.json` 손상 시:
   - `backup/snapshots/` 에 이전 스냅샷이 있는지 확인
   - 없으면 컷별 VIDEO/AUDIO/SUBTITLE을 UI에서 다시 생성
4. export 경로만 꼬인 경우: [debug_final_export.md](debug_final_export.md) 참고

## 백업에서 되돌리기

Phase 0~1 이후 루트 임시 파일은 `backup/temp_files/`에 있습니다.  
프로젝트 본문은 `projects/`에 그대로 두었습니다 — **projects/ 폴더는 이동하지 않음**.

## 위험 작업

- `projects/` 통째 삭제 금지
- slug 디렉터리 이름 변경 시 API·메타 불일치
