# backup/

프로젝트 루트를 깨끗하게 유지하기 위한 **보관 전용** 디렉터리입니다.  
여기로 옮긴 파일은 **삭제하지 않고** 보관합니다.

## 하위 폴더

| 경로 | 용도 |
|------|------|
| `temp_files/` | 세션 덤프, WSL 테스트 출력, 일회성 셸 스크립트 |
| `old_versions/` | Phase 0에서 옮긴 구 `archive/` 트리 |
| `archive_runs/` | 루트 `archive/cleanup_*` 런타임 보관 (이동만, 삭제 없음) |
| `snapshots/` | 세션 메모·Phase 인벤토리 |

## archive 정리 원칙

- **삭제 금지** — `backup/`으로만 이동.
- 루트 `archive/` 폴더는 `main.py`의 `ARCHIVE_DIR` 때문에 **남겨 둠** (비어 있어도 됨).
- 새 cleanup 실행 시에도 서버는 루트 `archive/`에 쓸 수 있음. 이후 수동으로 `archive_runs/`로 옮기면 됨.

## 스냅샷

- [snapshots/PHASE0-2026-06-03-inventory.md](snapshots/PHASE0-2026-06-03-inventory.md)
- [snapshots/PHASE2-2026-06-03-layout.md](snapshots/PHASE2-2026-06-03-layout.md)
