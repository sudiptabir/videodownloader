"""
Launcher for the Video Downloader desktop app.

Starts the Flask app behind a production WSGI server (waitress), opens the
default browser to the local UI, and keeps running until the console window
is closed. This is the entry point bundled into the Windows .exe.
"""

from __future__ import annotations

import sys
import threading
import webbrowser

HOST = "127.0.0.1"
PORT = 5000
URL = f"http://{HOST}:{PORT}/"


def _open_browser() -> None:
    webbrowser.open(URL)


def main() -> None:
    # Import here so PyInstaller picks up the app and its templates.
    from app import app

    print("=" * 56)
    print(" Video Downloader")
    print(f" Open your browser to: {URL}")
    print(" (A browser window should open automatically.)")
    print(" Keep this window open while using the app.")
    print("=" * 56)

    threading.Timer(1.2, _open_browser).start()

    try:
        from waitress import serve

        serve(app, host=HOST, port=PORT, threads=8)
    except ImportError:
        # Fallback to Flask's built-in server if waitress isn't available.
        app.run(host=HOST, port=PORT, debug=False)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
