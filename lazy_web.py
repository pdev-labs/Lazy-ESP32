#!/usr/bin/env python3
"""One-click launcher for Lazy-ESP32 Web version.
No terminal interaction needed: runs server and auto-opens browser.
Usage: python3 lazy_web.py [--port 8000] [--no-browser]
"""
import argparse
import threading
import time
import webbrowser
import sys

def main():
    ap = argparse.ArgumentParser(description="Lazy-ESP32 Web launcher")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        def _open():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    print(f"⚡ Lazy-ESP32 Web running at {url}")
    print("All CLI features available in the browser. Press Ctrl+C to stop.")
    try:
        import uvicorn
    except ImportError:
        print("Installing web dependencies...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "web/requirements-web.txt"])
        import uvicorn
    uvicorn.run("web.server:app", host=args.host, port=args.port, log_level="info")

if __name__ == "__main__":
    main()
