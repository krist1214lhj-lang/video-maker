# 스킬: 새 에이전트 추가

## 목적

`agents/` 패키지에 역할이 분리된 모듈을 추가하고, Director·API·프론트 버튼과 연결한다.

## 절차

### 1. 역할 정의

`AGENT.md` 3.1 번호(01~09) 중 어디에 속하는지 명시한다.  
**한 에이전트 = 한 책임.** (예: narration만, export만)

### 2. 모듈 생성

```text
agents/
  your_agent.py    # 비즈니스 로직만
```

규칙:

- **05 narration_subtitle** 모듈은 `export_agent` / `generate_project_final_export` import 금지
- **07 production(export)** 모듈은 narration/subtitle 생성 함수 import 금지
- `main.py`와의 순환 import 방지 → 무거운 import는 함수 내부 lazy import

### 3. Director 연결

자동 체인이 필요하면 **`pipeline_director.py`**에만 순서를 추가한다.  
개별 에이전트가 다른 에이전트 HTTP를 호출하지 않는다.

### 4. API 라우트 (`main.py`)

```python
@app.post("/agent/your-task/run")
def agent_your_task(request: YourRequest):
    from agents.your_agent import run_your_task
    ...
```

레거시 URL이 있으면 **내부에서 에이전트 위임**만 하고, 동작은 유지한다.

### 5. 프론트 (`templates/index.html`)

- 전용 버튼 → 전용 `fetch("/agent/...")` 함수
- Final export URL은 **Create Final Video / Generate All** 핸들러에만
- fetch guard에 새 export URL이 있으면 `isFinalExportUrl`에 추가

### 6. 문서·보고

- `AGENT.md` 3.2 표 갱신
- 완료 보고는 `AGENT.md` 8절 형식

## 체크리스트

- [ ] Audio+Subtitle 경로에서 export API 미호출
- [ ] `source` 검증 (export인 경우)
- [ ] 테스트: [test_pipeline.md](test_pipeline.md)
- [ ] 외부 패키지 추가 시 사용자 승인 (`AGENT.md` 4절)
