#!/usr/bin/env python3
import json
import urllib.error
import urllib.request

payload = {
    "project_id": "test-01",
    "cut_id": "cut_1",
    "cut_number": 1,
    "cut_index": 0,
    "provider": "replicate",
    "image_path": "/projects/test-01/images/cut_1.png",
    "video_prompt": "gentle cinematic camera move",
    "duration": 5,
    "video_debug": True,
}
body = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(
    "http://127.0.0.1:8011/generate-video-clip",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8")
        print("status=", resp.status)
        print(text)
except urllib.error.HTTPError as exc:
    print("status=", exc.code)
    print(exc.read().decode("utf-8"))
