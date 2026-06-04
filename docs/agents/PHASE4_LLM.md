# Phase 4A — 01·02 LLM 생성기 (Mock | GPT)

`main.py`·UI·영상 생성 **미연결**. `AGENT_LLM_MODE=gpt` 이어도 **API 키 없으면 자동 mock fallback**.

---

## 1. 클래스 구조 (Phase 4A)

```text
agents/
├── 01_topic_agent.py          # TopicAgentInput/Result, MockTopicGenerator, run_topic_agent()
├── 02_story_agent.py          # StoryAgentInput/Result, MockStoryGenerator, run_story_agent()
└── llm/
    ├── config.py              # resolve_effective_llm_mode() — 키 없으면 mock
    ├── openai_client.py       # OpenAIChatClient, parse_json_object
    ├── topic_generator.py     # GptTopicGenerator, create_topic_generator()
    └── story_generator.py     # GptStoryGenerator, create_story_generator()
```

| 계층 | 역할 |
|------|------|
| `TopicGenerator` / `StoryGenerator` | 에이전트가 의존하는 **Protocol** (Mock·GPT 동일 시그니처) |
| `MockTopicGenerator` / `MockStoryGenerator` | Phase 3A 규칙 기반 목 (에이전트 파일 내 유지) |
| `GptTopicGenerator` / `GptStoryGenerator` | `LLMClient` 1회 호출 → JSON 파싱 |
| `OpenAIChatClient` | `chat.completions` + `response_format: json_object` |
| `create_*_generator()` | `AGENT_LLM_MODE`에 따라 구현체 선택 |

---

## 2. 입출력 정의

### 01_topic_agent

**입력 (`TopicAgentInput`)**

| 필드 | 타입 | 설명 |
|------|------|------|
| `main_topic` | str | 대주제 (필수) |
| `style` | str | 영상 스타일 |
| `duration_seconds` | int | 목표 길이(초) |
| `cut_count` | int | 컷 수 |
| `project_slug` | str | 프로젝트 식별자 |
| `locale` | str | 프롬프트 언어 (`ko` 기본) |

**출력 (`TopicAgentResult`)**

| 필드 | 타입 | 설명 |
|------|------|------|
| `success` | bool | 생성 성공 여부 |
| `subtopics` | `SubTopic[3]` | `subtopic_1` ~ `subtopic_3` |
| `selected_subtopic_id` | str \| None | Director가 설정 (01 단독 호출 시 null) |
| `meta.generator` | str | 예: `MockTopicGenerator`, `GptTopicGenerator(gpt-4o-mini)` |
| `meta.llm_mode` | str | `mock` \| `gpt` |
| `meta.llm_usage` | object | GPT 시 토큰 사용량 |

**`SubTopic`**

| 필드 | 설명 |
|------|------|
| `id` | `subtopic_1` \| `subtopic_2` \| `subtopic_3` |
| `title` | 소주제 제목 |
| `angle` | `relationship` \| `place` \| `emotion` |
| `hook` | 오프닝 훅 |
| `one_line_pitch` | 한 줄 피치 |

### 02_story_agent

**입력 (`StoryAgentInput`)**

| 필드 | 타입 | 설명 |
|------|------|------|
| `main_topic` | str | 대주제 |
| `subtopic` | `SubTopic` | Director가 선택한 소주제 1개 |
| `style` | str | 스타일 |
| `duration_seconds` | int | 길이 |
| `cut_count` | int | 컷 수 |

**출력 (`StoryAgentResult`)**

| 필드 | 설명 |
|------|------|
| `variants` | 길이 3, 톤 `{comic, emotional, twist}` 각 1개 |
| `meta.llm_usage` | GPT 호출 시 토큰 |

**`StoryVariant` (톤당 1개)**

| 필드 | 설명 |
|------|------|
| `tone` | `comic` \| `emotional` \| `twist` |
| `title`, `summary`, `story_arc` | 스토리 요약 |
| `cut_flow` | `StoryCutBeat[]` — `cut`, `scene`, `emotion`, `narration`, `subtitle` |

---

## 3. Mock / GPT 전환 방법

### 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AGENT_LLM_MODE` | `mock` | `mock` 또는 `gpt` (`openai` 동일) |
| `OPENAI_API_KEY` | — | GPT 모드 시 **필수** |
| `AGENT_LLM_MODEL` | — | 공통 모델 (미설정 시 `gpt-4o-mini`) |
| `AGENT_LLM_TOPIC_MODEL` | — | 01 전용 모델 오버라이드 |
| `AGENT_LLM_STORY_MODEL` | — | 02 전용 모델 오버라이드 |

### Mock (기본, 비용 0)

```bash
# .env 또는 셸
AGENT_LLM_MODE=mock
```

### GPT

```bash
AGENT_LLM_MODE=gpt
OPENAI_API_KEY=sk-...
# 선택
AGENT_LLM_MODEL=gpt-4o-mini
```

### API 키 없을 때 (자동 fallback)

`AGENT_LLM_MODE=gpt` 이지만 `OPENAI_API_KEY`가 비어 있으면 **MockTopicGenerator / MockStoryGenerator** 로 동작한다.  
응답 `meta` 예: `llm_mode: "mock"`, `llm_mode_requested: "gpt"`, `llm_fallback_reason: "missing_openai_api_key"`.

### 코드에서 명시 (테스트·스크립트)

```python
import importlib

topic = importlib.import_module("agents.01_topic_agent")
result = topic.run_topic_agent(
    topic.TopicAgentInput(main_topic="애견카페 뽀식이"),
    llm_mode="gpt",  # env 무시
)
```

생성기 직접 주입:

```python
from agents.llm import create_topic_generator

gen = create_topic_generator("mock")
topic.run_topic_agent(inp, generator=gen)
```

Director / `run-demo`는 `llm_mode`를 넘기지 않으므로 **프로세스 환경 변수만** 따른다. 서버 재시작 후 Swagger에서 GPT를 쓰려면 uvicorn 프로세스에 `AGENT_LLM_MODE=gpt`가 있어야 한다.

---

## 4. OpenAI 호출 형태 (인터페이스)

- **클라이언트**: `OpenAIChatClient.complete_json(ChatCompletionRequest)`
- **API**: Chat Completions, `response_format: { type: json_object }`
- **호출 횟수**: 소주제 1회 + (선택 소주제당) 스토리 1회  
  Director 기본 경로: **2회/파이프라인** (01 + 02)

GPT가 반환해야 하는 JSON 스키마는 `agents/llm/prompts.py` 주석과 동일하다. 파싱 실패 시 `LLMClientError` → 에이전트 `success: false`, `meta.error`에 메시지.

---

## 5. 예상 비용 (2025–2026 기준, OpenAI 공개 단가 근사)

기본 모델 **`gpt-4o-mini`** (input ~$0.15 / 1M tokens, output ~$0.60 / 1M tokens).

| 단계 | 대략 토큰 (in / out) | 1회 비용 (USD) |
|------|----------------------|----------------|
| 01 소주제 3개 | ~600 / ~500 | ~$0.0004 |
| 02 스토리 3톤 × N컷(5) | ~900 / ~2,500 | ~$0.0016 |
| **01+02 합계** | — | **~$0.002 ~ $0.003** |

| 모델 | 01+02 1회 (대략) | 비고 |
|------|------------------|------|
| `gpt-4o-mini` | **$0.002~0.005** | Phase 4 기본 권장 |
| `gpt-4o` | **$0.03~0.08** | 품질↑, 약 15~20× |
| `gpt-4.1-mini` | `gpt-4o-mini`와 유사 | 모델명만 env에 지정 |

- **Mock**: $0, 네트워크 없음.
- **run-demo 전체 01→08**: 03~08은 아직 Mock이므로 LLM 비용은 **01+02만** 발생.
- 컷 수·주제 길이가 크면 02 output 토큰이 늘어 **$0.01~0.02**까지 가능.

실제 청구는 `meta.llm_usage` (`prompt_tokens`, `completion_tokens`)로 확인한다.

---

## 6. 검증 (로컬)

```bash
# Mock
AGENT_LLM_MODE=mock python3 -c "
import importlib
t=importlib.import_module('agents.01_topic_agent')
r=t.run_topic_agent(t.TopicAgentInput(main_topic='테스트'))
print(r.success, r.meta)
"

# GPT (키 필요)
AGENT_LLM_MODE=gpt OPENAI_API_KEY=sk-... python3 -c "
import importlib
t=importlib.import_module('agents.01_topic_agent')
r=t.run_topic_agent(t.TopicAgentInput(main_topic='테스트'))
print(r.success, r.meta.get('llm_usage'))
"
```

---

## 7. Phase 4 범위 밖 (다음 단계)

- `main.py` / UI에서 `AGENT_LLM_MODE` 노출
- 03~08 GPT화
- 영상·이미지 provider와 LLM 레이어 통합
- `run-demo` 요청 body에 `llm_mode` 필드 추가
