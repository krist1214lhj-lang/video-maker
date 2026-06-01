# Video Plan API

동영상 자동 생성 프로그램의 1단계 버전입니다.

사용자가 영상 주제, 스타일, 전체 길이를 입력하면 서버가 5컷짜리 영상 구성안을 JSON으로 반환합니다.
각 컷에는 OpenAI Images API로 생성한 이미지, 이미지 생성 프롬프트, fallback 샘플 이미지 URL도 포함됩니다.
영화 콘티처럼 shot type, lens, camera movement, lighting, mood color 정보와 촬영감독 수준의 shot breakdown, AI 영상 생성용 start/end frame prompt, camera path, motion layer, platform-specific export prompt, cinematic continuity system, transition, sound design, background music, pacing 정보도 함께 제공합니다.
Generate Video 버튼을 누르면 Runway 스타일의 영상 생성 작업 구조가 `video_jobs/{job_id}` 아래 생성됩니다. 각 컷은 `prompt.json`, `status.json`을 가지며, production export는 `generated_clips/{job_id}` 아래 생성됩니다. 컷별 JSON, prompt txt, continuity notes, mp4 슬롯, `render_manifest.json`, `storyboard_export.json`, `timeline_export.json`, export package zip이 함께 준비됩니다.

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 환경 변수

OpenAI Images API를 사용하려면 프로젝트 루트에 `.env` 파일을 만들고 API 키를 설정합니다.

```bash
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_IMAGE_MODEL=gpt-image-1.5
OPENAI_IMAGE_QUALITY=medium
FAL_KEY=your_fal_key_here
REPLICATE_API_TOKEN=your_replicate_api_token_here
RUNWAY_API_KEY=your_runway_api_key_here
PUBLIC_BASE_URL=https://your-ngrok-domain.ngrok-free.app
REPLICATE_VIDEO_MODEL=kwaivgi/kling-v2.1
```

이미지 생성 결과는 `generated_images/cut_1.png` 형식으로 저장됩니다. API 키가 없거나 생성에 실패하면 placeholder 이미지가 표시됩니다.
영상 작업 구조는 `video_jobs/job_.../cuts/cut_01/`와 `generated_clips/job_.../cut_01/` 형식으로 준비됩니다. 현재 구현은 외부 영상 생성 API 호출 전 단계의 pipeline shell이며, 생성 어댑터를 붙일 수 있도록 prompt/status/export/mp4 경로를 먼저 만듭니다.
컷별 Generate Video 버튼은 `/generate-video-clip`을 호출합니다. 기본 provider는 `mock`이며, 실제 API 대신 `generated_clips/job_.../` 아래 placeholder mp4, `video_prompt.txt`, `clip_manifest.json`, `job_manifest.json`을 생성합니다. `video_providers/` 아래의 `fal_provider.py`, `replicate_provider.py`, `runway_provider.py`는 실제 image-to-video API 연결을 위한 adapter scaffold입니다.
Replicate provider를 사용할 때는 `REPLICATE_API_TOKEN`과 ngrok 공개 주소인 `PUBLIC_BASE_URL` 또는 `NGROK_URL`이 필요합니다. 예: `PUBLIC_BASE_URL=https://현재-ngrok주소.ngrok-free.app`. 로컬 `/generated_images/cut_1.png`는 이 공개 주소를 붙여 Replicate에 전달됩니다.

## 실행

```bash
python start_server.py
```

서버 기본 주소:

```text
http://127.0.0.1:8011
```

직접 uvicorn을 실행할 때도 포트는 반드시 8011을 사용합니다. 포트 충돌 시 다른 포트로 fallback하지 않고 에러를 확인한 뒤 8011을 비워야 합니다.

```bash
uvicorn main:app --host 127.0.0.1 --port 8011 --reload
```

헬스 체크:

```bash
curl http://127.0.0.1:8011/health
```

ngrok 연결도 8011 기준으로 실행합니다.

```bash
ngrok http 8011
```

## 엔드포인트

### GET /

HTML 화면을 렌더링합니다. 브라우저에서 접속하면 주제, 스타일, 길이를 입력하고 5컷 영상 구성안을 카드 형태로 확인할 수 있습니다.

### GET /health

서버 상태 확인용 엔드포인트입니다.

응답:

```json
{
  "status": "ok",
  "server_port": 8011
}
```

### POST /video-plan

5컷짜리 영상 구성안을 생성합니다.

요청 예시:

```json
{
  "topic": "비 오는 날 혼자 사는 집으로 돌아가는 장면",
  "style": "감성적인 건축 영상",
  "duration": 30
}
```

`duration`은 5컷 구성을 위해 최소 5초 이상이어야 합니다.

응답 예시:

```json
{
  "topic": "비 오는 날 혼자 사는 집으로 돌아가는 장면",
  "style": "감성적인 건축 영상",
  "duration": 30,
  "cuts": [
    {
      "cut_number": 1,
      "scene_description": "영상의 분위기를 여는 도입 장면. '비 오는 날 혼자 사는 집으로 돌아가는 장면'의 공간과 시간대가 천천히 드러난다.",
      "narration": "오늘의 이야기는 비 오는 날 혼자 사는 집으로 돌아가는 장면에서 시작됩니다.",
      "subtitle": "비 오는 날 혼자 사는 집으로 돌아가는 장면",
      "shot_type": "Establishing Wide Shot",
      "lens": "24mm wide angle",
      "movement": "Slow dolly in",
      "lighting": "Soft ambient light with practical highlights",
      "mood_color": "#6f7f8d",
      "video_prompt": "감성적인 건축 영상, 조용한 도입부, 넓은 화면, 부드러운 카메라 이동, 현실적인 조명",
      "motion_prompt": "AI video motion prompt: cinematic camera language, 24mm wide establishing shot, slow steadicam dolly-in on a shallow diagonal camera path, realistic inertia with soft acceleration and gentle settle, layered environmental motion across foreground rain or haze, midground subject presence, and deep background light movement, strong spatial depth, emotional pacing begins quiet and expands gradually.",
      "start_frame_prompt": "감성적인 건축 영상, 비 오는 날 혼자 사는 집으로 돌아가는 장면, first frame, wide cinematic establishing composition, foreground atmospheric layer, small subject presence, deep background space, realistic film lighting, 16:9, no text",
      "end_frame_prompt": "감성적인 건축 영상, 비 오는 날 혼자 사는 집으로 돌아가는 장면, final frame of the shot, camera closer to the main environment, foreground parallax clearer, subject or entry path established, deep cinematic depth, realistic lighting, 16:9, no text",
      "camera_path": "Slow diagonal dolly-in from wide to medium-wide, steadicam-stable with subtle downward tilt correction and soft inertia at start and stop",
      "environmental_motion": "Rain, haze, curtain movement, practical light flicker, and distant background activity drift at different depth speeds",
      "subject_motion": "Subject remains small or enters slowly into the midground, no sudden gesture, movement supports the reveal",
      "motion_intensity": "Low intensity, gradual reveal, avoid fast object motion or aggressive camera drift",
      "transition_style": "Fade in from black into atmospheric establish",
      "continuity_notes": "Preserve weather direction, color temperature, and screen direction for the next detail cut",
      "runway_prompt": "감성적인 건축 영상, 비 오는 날 혼자 사는 집으로 돌아가는 장면, Establishing Wide Shot, 24mm wide angle...",
      "kling_prompt": "감성적인 건축 영상, 비 오는 날 혼자 사는 집으로 돌아가는 장면, Establishing Wide Shot, 24mm wide angle...",
      "veo_prompt": "Create a filmic video shot: 감성적인 건축 영상, 비 오는 날 혼자 사는 집으로 돌아가는 장면...",
      "pika_prompt": "감성적인 건축 영상, 비 오는 날 혼자 사는 집으로 돌아가는 장면, Establishing Wide Shot...",
      "previous_shot_relation": "Opening shot; establishes geography, mood, and visual grammar before any character-level detail.",
      "next_shot_relation": "Hands off to Cut 2 by pushing from broad spatial context into intimate tactile detail.",
      "emotional_transition": "Neutral atmosphere to quiet anticipation.",
      "camera_transition": "Dolly-in energy resolves into a closer handheld observational drift.",
      "spatial_transition": "Wide environment compresses into a foreground-obscured medium detail plane.",
      "pacing_curve": "Slow rise / exposition",
      "framing": "Wide 16:9 establishing frame with generous negative space",
      "foreground": "Soft architectural edge, rain streak, doorway, window frame, or passing texture for parallax",
      "midground": "Primary subject or entry path placed small within the environment",
      "background": "Deep spatial layer with practical lights, weather, skyline, corridor, or interior depth",
      "camera_height": "Chest to eye level, slightly below neutral for scale",
      "subject_direction": "Subject moves inward or remains still while the camera approaches",
      "emotional_intensity": "Quiet anticipation",
      "visual_focus": "Spatial reveal and emotional geography",
      "composition_rule": "Rule of thirds with leading lines into the subject",
      "start_frame_description": "Black resolves into a wide exterior or spatial establishing frame where '비 오는 날 혼자 사는 집으로 돌아가는 장면' is still distant and atmospheric.",
      "end_frame_description": "The camera settles closer to the main environment, with foreground depth and the central visual subject clearly established.",
      "motion_strength": "Low",
      "camera_speed": "0.35x slow cinematic drift",
      "transition_duration": "1.2s",
      "cinematic_pacing": "Opening beat with a calm inhale, gradual information reveal, no early visual climax.",
      "transition": "Fade in from black with a soft atmospheric dissolve",
      "sound_design": "Low room tone, distant ambience, soft environmental texture, restrained first cue hit",
      "background_music": "Minimal low piano pad, sparse notes, warm analog texture",
      "pacing": "Slow reveal, 4-6 second hold before the next cut",
      "image_prompt": "감성적인 건축 영상, 비 오는 날 혼자 사는 집으로 돌아가는 장면, establishing shot, cinematic lighting, 16:9 frame",
      "image_url": "/generated_images/cut_1.png",
      "image_status": "generated",
      "image_error": null,
      "sample_image_url": "/static/placeholders/cut-1.svg",
      "recommended_duration": 6
    }
  ]
}
```

### POST /generate-video-clip

생성된 이미지와 motion prompt를 받아 provider adapter를 통해 컷 단위 image-to-video 작업을 생성합니다.

현재 기본 동작은 `mock` provider입니다. 실제 fal.ai, Replicate, Runway 호출은 adapter 파일에 구현하면 됩니다.
`provider`를 `replicate`로 보내면 `kwaivgi/kling-v2.1` 모델을 기본값으로 사용해 Replicate API를 호출합니다. 로컬 이미지 경로만 있고 공개 ngrok URL이 설정되어 있지 않으면 명확한 에러를 반환합니다. Replicate 호출에는 `prompt`, `start_image`, `duration`, `negative_prompt`를 전달합니다. 결과가 video URL이면 `cut_x_replicate.mp4`로 다운로드하고, 다운로드 실패 시 URL만 manifest에 보존합니다.

요청 예시:

```json
{
  "cut_number": 1,
  "image_path": "/generated_images/cut_1.png",
  "motion_prompt": "Slow cinematic dolly-in with rain and window reflections",
  "duration": 6,
  "provider": "mock"
}
```

응답 예시:

```json
{
  "job_id": "job_20260527_120000_ab12cd34",
  "status": "mock_completed",
  "clip_path": "generated_clips/job_20260527_120000_ab12cd34/cut_1_mock.mp4",
  "provider": "mock",
  "message": "Mock video clip completed. Placeholder mp4 was written to generated_clips.",
  "manifest_file": "generated_clips/job_20260527_120000_ab12cd34/job_manifest.json",
  "clip_url": "/generated_clips/job_20260527_120000_ab12cd34/cut_1_mock.mp4",
  "prompt_txt_file": "generated_clips/job_20260527_120000_ab12cd34/cut_1_video_prompt.txt",
  "clip_manifest_file": "generated_clips/job_20260527_120000_ab12cd34/cut_1_clip_manifest.json"
}
```

Provider 교체 구조:

```text
video_providers/
  base.py              공통 VideoProvider 인터페이스
  mock_provider.py     로컬 placeholder mp4/manifest 생성
  fal_provider.py      fal.ai 연결 준비
  replicate_provider.py Replicate 연결 준비
  runway_provider.py   Runway 연결 준비
```

Replicate 결과 파일:

```text
generated_clips/
  job_YYYYMMDD_HHMMSS_xxxxxxxx/
    cut_1_video_prompt.txt
    cut_1_replicate_result.json
    cut_1_replicate.mp4
    cut_1_clip_manifest.json
    job_manifest.json
```

실제 응답에는 총 5개의 컷이 포함됩니다.

### POST /video-jobs

생성된 storyboard를 기준으로 영상 생성 작업 폴더를 만듭니다.

요청 형식:

```json
{
  "plan": {
    "topic": "...",
    "style": "...",
    "duration": 30,
    "cuts": []
  }
}
```

응답에는 `job_id`, `job_dir`, `clips_dir`, `manifest_file`, `render_manifest_file`, `storyboard_export_url`, `timeline_export_url`, `export_package_url`, 컷별 `prompt_file`, `clip_json_file`, `prompt_txt_file`, `continuity_notes_file`, `video_file`, `video_status`, `render_progress`, `clip_duration`, `estimated_render_time`이 포함됩니다.

생성 구조:

```text
video_jobs/
  job_YYYYMMDD_HHMMSS_xxxxxxxx/
    manifest.json
    storyboard.json
    timeline.json
    cuts/
      cut_01/
        prompt.json
        status.json

generated_clips/
  job_YYYYMMDD_HHMMSS_xxxxxxxx/
    storyboard_export.json
    timeline_export.json
    render_manifest.json
    job_YYYYMMDD_HHMMSS_xxxxxxxx_export_package.zip
    cut_01/
      cut_01.json
      prompt.txt
      continuity_notes.txt
      cut_01.mp4
```

### GET /video-jobs/{job_id}

생성된 video job manifest와 컷별 generation status를 조회합니다.
