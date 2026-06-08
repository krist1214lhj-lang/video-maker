# Phase 4B — 03 Character Agent GPT화 계획

범위: `agents/03_character_agent.py` 분석 및 GPT 적용 설계.

주의: 이 문서는 계획서이며, 코드 수정 내용이 아니다. `main.py`, FastAPI, templates 연결은 Phase 4B 범위 밖으로 둔다.

## 1. 현재 구조 분석

`agents/03_character_agent.py`는 현재 Mock 기반 Character Agent다.

역할:

- 고정 캐릭터 관리 (`reference_characters/`)
- `reference_character` 슬롯과 스토리 맥락을 바탕으로 `character_prompt` 생성
- `character_profile`, `visual_constraints` 생성

핵심 타입:

| 타입 | 역할 |
|------|------|
| `StoryContext` | 02 Story 결과 중 선택된 스토리를 Character용으로 축약한 입력 컨텍스트 |
| `ReferenceCharacter` | `reference_characters/{name}` 슬롯 입력 |
| `CharacterProfile` | 캐릭터 이름, 요약, 레퍼런스 경로, identity lock 강도 등 출력 |
| `VisualConstraints` | 컷 생성 시 유지/변경 가능한 비주얼 제약 |
| `CharacterAgentInput` | Character Agent 입력 |
| `CharacterAgentResult` | Character Agent 출력 |
| `CharacterPromptBuilder` | Mock/GPT 교체 지점 Protocol |
| `MockCharacterPromptBuilder` | 현재 실제 생성기 |

현재 `run_character_agent()`는 `llm_mode` 인자가 없고, builder를 직접 주입하지 않으면 항상 `MockCharacterPromptBuilder()`를 사용한다.

## 2. 입력/출력 구조

### 입력: `CharacterAgentInput`

| 필드 | 타입 | 설명 |
|------|------|------|
| `topic` | `str` | 메인 토픽 |
| `story` | `StoryContext` | 선택된 스토리 컨텍스트 |
| `reference_character` | `ReferenceCharacter` | 고정 캐릭터 입력 |
| `project_slug` | `str` | 프로젝트 식별자 |
| `reference_characters_dir` | `Path | None` | 레퍼런스 캐릭터 루트 override |

### 입력: `ReferenceCharacter`

| 필드 | 타입 | 설명 |
|------|------|------|
| `name` | `str` | 캐릭터 디렉터리명. 예: `bposik_v2` |
| `display_name` | `str` | 표시 이름. 없으면 name 기반 생성 |
| `species_or_type` | `str` | 종/타입 |
| `visual_traits` | `str` | 외형 특징 |
| `style_notes` | `str` | 스타일 메모 |
| `identity_lock_strength` | `str` | 기본값 `high` |

### 파일 기반 보조 입력

기본 루트:

```text
reference_characters/
```

이미지 확장자:

```text
.png, .jpg, .jpeg, .webp, .gif
```

우선 이미지명:

```text
main.png
main.jpg
reference.png
reference.jpg
```

메모리 파일:

```text
reference_characters/{name}/character_memory.json
```

### 출력: `CharacterAgentResult`

| 필드 | 설명 |
|------|------|
| `success` | 성공 여부 |
| `character_profile` | `CharacterProfile | None` |
| `character_prompt` | 컷/이미지 생성에 사용할 캐릭터 프롬프트 |
| `visual_constraints` | `VisualConstraints | None` |
| `generated_at` | UTC ISO timestamp |
| `meta` | builder, reference count, memory 여부 등 |

현재 `meta`:

| 키 | 설명 |
|----|------|
| `reference_image_count` | 발견된 레퍼런스 이미지 수 |
| `memory_loaded` | `character_memory.json` 로드 여부 |
| `builder` | 사용 builder 클래스명 |

## 3. Mock 동작 방식

`MockCharacterPromptBuilder`는 규칙 기반 문자열 조합으로 동작한다.

처리 흐름:

1. `ReferenceCharacter.name`을 trim한다.
2. `display_name`이 없으면 `name.replace("_", " ").title()`로 만든다.
3. `character_memory.json`이 있으면 `character_prompt`를 `memory_prompt`로 읽는다.
4. `StoryContext`에서 tone, story arc, subtopic title을 story hook으로 만든다.
5. 기본 identity lock rule을 만든다.
6. `visual_traits`, `style_notes`가 있으면 rule에 추가한다.
7. `CharacterProfile`, `character_prompt`, `VisualConstraints`를 반환한다.

기본 유지 항목:

```text
face shape
fur color and pattern
body proportions
silhouette
```

기본 허용 변경 항목:

```text
expression
pose
gaze direction
action per cut
```

특징:

- 네트워크 호출 없음
- 비용 없음
- 결정적 결과
- 레퍼런스 이미지가 없어도 실패하지 않음
- `topic` 또는 `reference_character.name`이 비어 있으면 `success=False`

## 4. GPT 적용 설계안

Phase 4A의 Topic/Story 구조와 동일한 패턴을 따른다.

현재 Phase 4A 패턴:

```text
agents/
├── 01_topic_agent.py
├── 02_story_agent.py
└── llm/
    ├── config.py
    ├── openai_client.py
    ├── topic_generator.py
    └── story_generator.py
```

Phase 4B 권장 구조:

```text
agents/
├── 03_character_agent.py
└── llm/
    ├── config.py
    ├── openai_client.py
    └── character_generator.py
```

### 4.1 `03_character_agent.py`

`run_character_agent()`에 `llm_mode` 인자를 추가한다.

```python
def run_character_agent(
    input_data: CharacterAgentInput,
    *,
    builder: CharacterPromptBuilder | None = None,
    llm_mode: str | None = None,
) -> CharacterAgentResult:
    ...
```

동작:

- `builder`가 직접 주입되면 기존처럼 그 builder를 우선 사용한다.
- `builder is None`이면 `create_character_prompt_builder(llm_mode)`로 Mock/GPT를 선택한다.
- `LLMClientError` 발생 시 `success=False`로 반환한다.
- 성공 meta에는 `llm_mode`, `llm_mode_requested`, `llm_fallback_reason`을 포함한다.
- GPT 성공 시 `llm_model`, `llm_usage`, `llm_provider`를 포함한다.

### 4.2 `agents/llm/config.py`

Character 모델 설정을 추가한다.

```python
DEFAULT_CHARACTER_MODEL = "gpt-4o-mini"

def resolve_character_model() -> str:
    return (
        os.getenv("AGENT_LLM_CHARACTER_MODEL")
        or os.getenv("AGENT_LLM_MODEL")
        or DEFAULT_CHARACTER_MODEL
    ).strip()
```

환경 변수:

| 변수 | 설명 |
|------|------|
| `AGENT_LLM_MODE` | `mock` 또는 `gpt` |
| `OPENAI_API_KEY` | GPT 모드 필수. 없으면 Mock fallback |
| `AGENT_LLM_MODEL` | 공통 모델 override |
| `AGENT_LLM_CHARACTER_MODEL` | Character 전용 모델 override |

### 4.3 신규 `agents/llm/character_generator.py`

구성:

- `GptCharacterPromptBuilder`
- `create_character_prompt_builder(mode)`
- `builder_mode_label(builder, resolved=...)`
- `_character_system_prompt()`
- `_character_user_prompt(input_data, reference_images, memory_payload)`
- `_parse_character_payload(text, input_data, reference_images, memory_payload)`

`GptCharacterPromptBuilder`는 `CharacterPromptBuilder` Protocol을 구현한다.

```python
@dataclass
class GptCharacterPromptBuilder:
    client: LLMClient
    model: str | None = None
    temperature: float = 0.5

    def build(
        self,
        input_data: CharacterAgentInput,
        *,
        reference_images: list[str],
        memory_payload: dict[str, Any] | None,
    ) -> tuple[CharacterProfile, str, VisualConstraints]:
        ...
```

### 4.4 GPT JSON 스키마

GPT 응답은 JSON object만 허용한다.

```json
{
  "character_profile": {
    "character_name": "bposik_v2",
    "display_name": "Bposik V2",
    "species_or_type": "cream/apricot toy poodle",
    "character_summary": "...",
    "identity_lock_strength": "high"
  },
  "character_prompt": "...",
  "visual_constraints": {
    "style_lock_rules": ["..."],
    "must_preserve": ["face shape", "fur color and pattern"],
    "allowed_variations": ["expression", "pose"]
  }
}
```

Python 로직이 유지해야 하는 필드:

- `reference_dir`
- `memory_file`
- `reference_images`
- `public_reference_url`

이 필드들은 GPT가 만들지 않게 한다. 파일 경로와 public URL은 현재 코드의 deterministic 로직이 더 안전하다.

### 4.5 프롬프트 설계 원칙

System prompt:

- short-form video character continuity assistant
- valid JSON only
- preserve identity across cuts
- do not invent unavailable reference image details

User prompt 포함 정보:

- main topic
- story tone, title, summary, story arc, cut count
- reference character input
- reference image URLs
- memory payload 요약
- required JSON schema

제약:

- 캐릭터 정체성 보존 문장을 명확히 생성
- expression/pose/action은 컷별 변화 가능
- face/body/fur/silhouette은 보존
- 후속 이미지 생성 프롬프트에 바로 붙일 수 있는 문장형 `character_prompt` 생성

중요:

현재 구조에서는 GPT가 이미지를 직접 분석하지 않는다. 레퍼런스 이미지 경로는 텍스트 컨텍스트일 뿐이다. 따라서 "이미지에서 외형을 추출"하는 기능으로 설계하면 안 된다.

## 5. 변경 파일 목록

예상 변경 파일:

| 파일 | 변경 내용 |
|------|----------|
| `agents/03_character_agent.py` | `llm_mode` 인자 추가, builder factory 연결, LLM 에러/meta 처리 |
| `agents/llm/config.py` | `DEFAULT_CHARACTER_MODEL`, `resolve_character_model()` 추가 |
| `agents/llm/character_generator.py` | 신규 GPT Character builder |
| `agents/llm/__init__.py` | Character generator export 추가 |
| `agents/llm/pipeline_meta.py` | Character usage/cost 집계 확장 검토 |
| `agents/09_director_agent.py` | Character meta를 pipeline meta에 포함할 경우 확장 |
| `agents/README.md` | Phase 4B 설명 추가 |
| `docs/agents/PHASE4_LLM.md` | 03 Character GPT화 내용 추가 |

테스트를 추가한다면:

| 파일 | 내용 |
|------|------|
| `tests/agents/test_character_agent.py` | Mock, fallback, parser, fake LLMClient 테스트 |

현재 확인 시 `tests` 디렉터리는 존재하지 않았다. 테스트 디렉터리를 새로 만들지, 기존 프로젝트 테스트 위치가 따로 있는지는 다음 구현 단계에서 확정한다.

## 6. 테스트 방법

### 6.1 Mock 기본 동작

```bash
AGENT_LLM_MODE=mock python -c "
import importlib
m = importlib.import_module('agents.03_character_agent')
r = m.example_character_result()
print(r)
"
```

확인:

- `success=True`
- `character_prompt` 생성
- `character_profile.character_name == 'bposik_v2'`
- `visual_constraints` 존재

### 6.2 GPT 요청 + 키 없음 fallback

```bash
AGENT_LLM_MODE=gpt python -c "
import importlib
m = importlib.import_module('agents.03_character_agent')
r = m.example_character_result()
print(r)
"
```

구현 후 기대:

- 실제 모드 `mock`
- 요청 모드 `gpt`
- fallback reason `missing_openai_api_key`

### 6.3 Fake LLMClient 단위 테스트

실제 OpenAI 호출 없이 다음을 검증한다.

- 정상 JSON 파싱
- 누락 필드 fallback
- `CharacterProfile` 생성
- `VisualConstraints` 생성
- `llm_usage` meta 전달
- 파싱 실패 시 `LLMClientError`

### 6.4 실제 GPT smoke test

```bash
AGENT_LLM_MODE=gpt OPENAI_API_KEY=sk-... python -c "
import importlib
m = importlib.import_module('agents.03_character_agent')
r = m.example_character_result()
print(r['success'])
print(r.get('character_profile'))
"
```

주의:

- 실제 비용 발생
- network 필요
- smoke test는 수동 실행으로 제한 권장

### 6.5 Director 통합 검증

```bash
AGENT_LLM_MODE=mock python -c "
import importlib
d = importlib.import_module('agents.09_director_agent')
print(d.example_full_pipeline_result())
"
```

구현 후 확인:

- 기존 full pipeline이 깨지지 않아야 함
- Character가 기본 Mock으로 유지되어야 함
- `AGENT_LLM_MODE=gpt` 설정 시 Character도 동일 정책을 따르는지 확인

## 7. 위험도 분석

위험도: 중간.

### 7.1 주요 리스크

| 리스크 | 설명 | 영향 |
|--------|------|------|
| JSON 파싱 실패 | GPT가 스키마와 다른 응답을 반환 | `success=False` |
| 프롬프트 과잉 중복 | identity lock 문장이 너무 길거나 반복 | 후속 이미지 prompt 품질 저하 |
| 이미지 분석 오해 | 현재 GPT는 reference image를 직접 보지 않음 | 실제 외형 추출 기대와 불일치 |
| 후속 단계 호환성 | 04~07 Mock이 `character_prompt` 형태에 묵시적으로 의존 가능 | storyboard prompt 변화 |
| 비용 메타 누락 | `pipeline_meta.py`가 현재 Topic/Story만 집계 | Character GPT 비용이 응답 meta에 안 보일 수 있음 |
| Director 통합 불일치 | Character에 `llm_mode`를 넘기지 않는 현재 구조 | env 기반 정책으로 맞춰야 함 |
| 긴 출력 | GPT가 너무 긴 `character_prompt` 생성 | token/cost 증가, 이미지 prompt 중복 |

### 7.2 완화 방안

- 기본값은 계속 Mock으로 유지한다.
- `OPENAI_API_KEY` 없으면 Mock fallback을 유지한다.
- GPT 응답 스키마를 작게 유지한다.
- 파일 경로/이미지 URL 필드는 GPT가 아니라 Python 로직으로 확정한다.
- parser에서 필수 필드 fallback을 제공한다.
- fake client 단위 테스트를 먼저 작성한다.
- `character_prompt` 최대 길이 제한 또는 후처리 기준을 검토한다.
- `pipeline_meta.py`에 Character usage/cost를 추가할지 Phase 4B에서 함께 결정한다.

## 8. 구현 순서 제안

1. `agents/llm/config.py`에 Character 모델 설정 추가
2. `agents/llm/character_generator.py` 신규 작성
3. `agents/03_character_agent.py`에 `llm_mode`와 factory 연결
4. `agents/llm/__init__.py` export 추가
5. Mock/fallback/fake client 테스트
6. `pipeline_meta.py` Character 비용 집계 확장
7. 문서 업데이트
8. 실제 GPT smoke test

## 9. 결론

`03_character_agent`는 이미 `CharacterPromptBuilder` Protocol이 있어 GPT 전환 지점이 준비되어 있다. Phase 4A의 Topic/Story 패턴을 그대로 적용하면 구조 변경을 작게 유지할 수 있다.

Phase 4B의 핵심은 "레퍼런스 이미지 분석"이 아니라 "스토리 맥락 + 캐릭터 메모리 + 레퍼런스 슬롯을 기반으로 더 정교한 character prompt와 visual constraints를 생성"하는 것이다. Mock 기본값, API 키 누락 fallback, JSON parser 테스트를 유지하면 기존 파이프라인 안정성을 크게 해치지 않고 GPT화를 진행할 수 있다.
