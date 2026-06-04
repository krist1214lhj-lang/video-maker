# Generate Video Endpoint Debug Rules

> 구 `agent.md` 본문 (Phase 0~1에서 `skills/`로 이동). 루트 `agent.md`는 안내 stub만 유지.

## Generate Video Endpoint Debug Rules

- `/generate-video-clip` must first prove endpoint entry, JSON parsing, URL normalization, and public image reachability before calling Replicate.
- Do not call Replicate until `public_start_image_url` passes format validation (https, no backslashes, no localhost).
- Do not HTTP GET/HEAD the ngrok public URL from the same server during `/generate-video-clip` (avoids self-call timeout); Replicate fetches the URL directly.
- API connection timeout and Replicate generation timeout must be separate (20s connection / ping, 300s generation).
- Generate Video endpoint must always return JSON success or failure.
- Never leave frontend fetch pending without a response.

### Debug-only mode

- Set `GENERATE_VIDEO_DEBUG_ONLY=1` in the environment, pass `"video_debug": true` in the request body, or open the app with `?video_debug=1`.
- Debug mode returns immediately after URL normalization and reachability checks without calling Replicate.

### Required server logs

- `[api-generate-video-entered] /generate-video-clip`
- `[api-generate-video-before-json]`
- `[api-generate-video-after-json]`
- `[api-generate-video-data]`
- `[public-image-url-before-normalize]`
- `[public-image-url-after-normalize]`
- `[api-generate-video-validate-start]`
- `[api-generate-video-url-normalized]`
- `[api-generate-video-url-check-start]`
- `[api-generate-video-url-check-result]`
- `[api-generate-video-replicate-start]` (only when proceeding to generation)
- `[replicate-poll]`, `[replicate-complete]`, `[replicate-failed]`

### Public image URL

- Reserved ngrok domain: `https://discern-statute-viability.ngrok-free.dev` (note: **statute**, not statue).
- Paths must use forward slashes only; backslashes in URLs are rejected.
