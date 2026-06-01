#!/bin/bash
set +e
cd /home/hyunjun/codex-project || exit 1
echo "=== 1. py_compile ==="
python3 -m py_compile main.py video_editor/project_final_export.py 2>&1
echo py_compile_exit:$?
echo "=== 2. export ==="
./venv/bin/python -c "from main import resolve_project_dir, generate_project_final_export; slug, d = resolve_project_dir(\"bposik_test\"); result = generate_project_final_export(slug, d); print(\"SUCCESS\"); print(\"output\", result.get(\"output_file\")); print(\"duration\", result.get(\"duration\")); print(\"resolution\", result.get(\"resolution\")); print(\"file_size\", result.get(\"file_size\")); print(\"ffmpeg_log\", result.get(\"ffmpeg_log_file\"))" 2>&1
echo export_exit:$?
echo "=== 3. ls exports ==="
ls -la projects/bposik_test/exports/ 2>&1
echo "=== 4. ffprobe ==="
ffprobe -v error -show_entries format=duration,size -of default=nw=1 projects/bposik_test/exports/final_export.mp4 2>&1
echo "=== 5. ffmpeg log head ==="
head -80 projects/bposik_test/exports/ffmpeg_export.log 2>&1
