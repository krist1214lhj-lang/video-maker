# 스킬: 안전 폴더 정리 (Phase 2)

## 목적

AUTO_VIDEO_MAKER 스타일로 **문서·agents·backup**만 정리하고, 실행·URL·outputs는 그대로 둡니다.

## 허용

- `agents/post_production/`, `agents/planned/` 구조
- `skills/`, `docs/`, `backup/` 문서·보관
- 루트 `archive/` **내용** → `backup/archive_runs/` 이동 (루트 `archive/` 폴더는 유지)

## 금지

- `main.py` 분해·경로 상수 변경
- `services/`, `pipelines/*.py` 신규
- URL·outputs·projects·`generated_outputs` 이동
- `templates/index.html` 구조 변경

## 검증

```bash
cd /home/hyunjun/codex-project
python3 -m py_compile main.py agents/*.py agents/post_production/*.py
curl -s http://127.0.0.1:8011/health   # 서버 기동 후
```

후반작업: [test_pipeline.md](test_pipeline.md)

## Git

1. 정리 **전** checkpoint 커밋
2. 정리 **후** `agents/`, `docs/`, `skills/`, `backup/` 만 스테이징해 커밋

참고: [../docs/STRUCTURE.md](../docs/STRUCTURE.md)
