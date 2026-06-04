"""
FastAPI 앱 엔트리 (개발 라우트 포함).

main.py 는 수정하지 않고, import 시점에 /agent/run-demo 만 추가한다.
uvicorn: `app_with_dev_routes:app` (start_server.py 기본)
"""

from main import app

from agents.dev_api import register_run_demo_routes

register_run_demo_routes(app)

__all__ = ["app"]
