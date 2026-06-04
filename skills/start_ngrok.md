# 스킬: ngrok 기동

## 목적

로컬 서버(포트 **8011**)를 공개 URL로 노출해 Replicate 등 외부 API가 이미지·영상 URL에 접근할 수 있게 합니다.

## 전제

- [start_server.md](start_server.md)로 서버가 `http://127.0.0.1:8011`에서 동작 중
- ngrok CLI 설치됨
- `.env`에 `PUBLIC_BASE_URL` 또는 `NGROK_URL` 설정

## NGROK 터미널에서 실행

```bash
cd /home/hyunjun/codex-project
ngrok http 8011
```

고정 도메인(프로젝트 기본값 예시):

```bash
bash scripts/restart-ngrok.sh
```

스크립트는 `NGROK_DOMAIN`, `NGROK_PORT`(기본 8011) 환경변수를 사용합니다.

## 확인

```bash
curl -s http://127.0.0.1:4040/api/tunnels
```

터널 `public_url`이 `.env`의 `PUBLIC_BASE_URL`과 일치하는지 확인합니다.

## 주의

- Generate Video 디버그 규칙: [debug_generate_video.md](debug_generate_video.md)
- 서버가 동일 ngrok URL에 self-GET 하면 타임아웃될 수 있음 — Replicate가 직접 fetch
