import threading
import time
import webbrowser
import subprocess
import platform
import os
import uvicorn

from main import SERVER_PORT

APP_URL = "http://127.0.0.1:8011"


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        release = platform.uname().release.lower()
        if "microsoft" in release or "wsl" in release:
            return True
    except Exception:
        pass
    return False


def open_browser_later(delay: float = 2.0) -> None:
    time.sleep(delay)
    try:
        opened = webbrowser.open(APP_URL, new=2)
        if opened and not is_wsl():
            return
    except Exception:
        opened = False

    # Fallback: try using PowerShell start (works from Windows and WSL)
    try:
        subprocess.run(["powershell.exe", "start", APP_URL], check=False)
    except Exception:
        # Last-resort: try system-specific commands
        if platform.system() == "Windows":
            try:
                subprocess.run(["start", APP_URL], shell=True, check=False)
            except Exception:
                print(f"Could not open browser to {APP_URL}")
        else:
            print(f"Could not open browser to {APP_URL}")


if __name__ == "__main__":
    # Start browser opener thread before launching the server
    t = threading.Thread(target=open_browser_later, args=(2.0,), daemon=True)
    t.start()

    uvicorn.run(
        "app_with_dev_routes:app",
        host="127.0.0.1",
        port=8011,
        reload=True,
    )
