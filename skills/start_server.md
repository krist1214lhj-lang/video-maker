# 스킬: 서버 기동

## 목적

로컬 API 서버를 **포트 8011**에서 실행한다.

## 전제

- 작업 디렉터리: `/home/hyunjun/codex-project`
- 가상환경 활성화 후 의존성 설치 완료 (`pip install -r requirements.txt`)

## SERVER 터미널에서 실행

```bash
cd /home/hyunjun/codex-project
source .venv/bin/activate   # Windows/WSL 환경에 맞게 조정
python start_server.py
```

또는:

```bash
uvicorn main:app --host 127.0.0.1 --port 8011 --reload
```

## 확인

```bash
curl http://127.0.0.1:8011/health
```

응답에 `"server_port": 8011` 이 포함되어야 한다.

## 주의

- 포트가 사용 중이면 **다른 포트로 바꾸지 말고** 8011을 점유한 프로세스를 종료한다.
- Replicate 등 외부 API 테스트 시 **NGROK 터미널**에서 `ngrok http 8011`을 별도로 실행한다.

## 실패 시

- `Address already in use` → `lsof -i :8011` 또는 `ss -tlnp | grep 8011`로 프로세스 확인
- import 오류 → `agents/` 패키지 누락 여부 확인 (`AGENT.md` 3.2절)
