"""
Video Downloader - a general-purpose desktop video downloader.

Backend: Flask + yt-dlp. Serves a local web UI, lists available formats for a
given URL, and downloads the selected quality as MP4 into the user's Downloads
folder.

IMPORTANT / RESPONSIBLE USE
---------------------------
This is a neutral, general-purpose tool. Only use it to download content you
are authorized to download: your own uploads, public-domain or Creative-Commons
material, content you have licensed, or platforms whose Terms of Service permit
downloading. Do not use it to download content in violation of a site's Terms
of Service or applicable copyright law.
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import yt_dlp

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Bundled ffmpeg discovery
# ---------------------------------------------------------------------------

def _base_dir() -> Path:
    """Directory that holds bundled resources, in both dev and frozen modes."""
    if getattr(sys, "frozen", False):
        # Running inside a PyInstaller bundle.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def get_ffmpeg_location() -> str | None:
    """Return the folder containing bundled ffmpeg.exe, if present."""
    candidate = _base_dir() / "bin"
    if (candidate / "ffmpeg.exe").exists() or (candidate / "ffmpeg").exists():
        return str(candidate)
    return None


FFMPEG_LOCATION = get_ffmpeg_location()

# ---------------------------------------------------------------------------
# Paths / state
# ---------------------------------------------------------------------------

def get_downloads_dir() -> Path:
    """Return the current user's Downloads directory, creating it if needed."""
    home = Path.home()
    downloads = home / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


# In-memory job registry: job_id -> progress dict
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **kwargs) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {})
        JOBS[job_id].update(kwargs)


def _get_job(job_id: str) -> dict | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job is not None else None


# ---------------------------------------------------------------------------
# Format discovery
# ---------------------------------------------------------------------------

def _human_size(num: int | float | None) -> str:
    if not num:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def list_formats(url: str) -> dict:
    """Probe a URL and return title + a de-duplicated list of quality options."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # Build a set of selectable "quality" options keyed by height.
    # We present combined height options (video+best audio) plus audio-only.
    heights: dict[int, dict] = {}
    has_audio_only = False

    for f in info.get("formats", []):
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        height = f.get("height")

        # Video streams (with or without audio)
        if vcodec and vcodec != "none" and height:
            existing = heights.get(height)
            size = f.get("filesize") or f.get("filesize_approx")
            if existing is None or (size and size > (existing.get("_size") or 0)):
                heights[height] = {
                    "height": height,
                    "label": f"{height}p",
                    "ext": "mp4",
                    "_size": size or 0,
                    "filesize": _human_size(size),
                    "fps": f.get("fps"),
                }
        # Detect availability of audio-only
        if (not vcodec or vcodec == "none") and acodec and acodec != "none":
            has_audio_only = True

    options = []
    for height in sorted(heights.keys(), reverse=True):
        h = heights[height]
        fps = h.get("fps")
        label = h["label"] + (f" {int(fps)}fps" if fps and fps >= 50 else "")
        options.append(
            {
                "id": f"video-{height}",
                "label": label,
                "type": "video",
                "height": height,
                "ext": "mp4",
                "filesize": h["filesize"],
            }
        )

    if has_audio_only:
        options.append(
            {
                "id": "audio",
                "label": "Audio only (MP3)",
                "type": "audio",
                "height": 0,
                "ext": "mp3",
                "filesize": "",
            }
        )

    return {
        "title": info.get("title") or "video",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "webpage_url": info.get("webpage_url") or url,
        "formats": options,
    }


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _format_selector(option_id: str) -> tuple[str, bool]:
    """Return (yt-dlp format string, is_audio_only) for a given option id."""
    if option_id == "audio":
        return "bestaudio/best", True
    if option_id.startswith("video-"):
        height = option_id.split("-", 1)[1]
        # Prefer mp4 video + m4a audio at or below the chosen height, merged.
        fmt = (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best"
        )
        return fmt, False
    return "best", False


def _run_download(job_id: str, url: str, option_id: str) -> None:
    downloads = get_downloads_dir()
    fmt, is_audio = _format_selector(option_id)

    def hook(d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            percent = (done / total * 100) if total else 0
            _set_job(
                job_id,
                status="downloading",
                percent=round(percent, 1),
                speed=d.get("speed"),
                eta=d.get("eta"),
            )
        elif status == "finished":
            # Download of a stream finished; merge/convert may still run.
            _set_job(job_id, status="processing", percent=100)

    outtmpl = str(downloads / "%(title)s.%(ext)s")
    ydl_opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "restrictfilenames": False,
        "windowsfilenames": True,
    }

    if FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION

    if is_audio:
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        # Ensure final container is mp4.
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["postprocessors"] = [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}
        ]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            final_ext = "mp3" if is_audio else "mp4"
            final_path = Path(filename).with_suffix("." + final_ext)
        _set_job(
            job_id,
            status="done",
            percent=100,
            filepath=str(final_path),
            filename=final_path.name,
        )
    except Exception as exc:  # noqa: BLE001 - surface any yt-dlp error to UI
        _set_job(job_id, status="error", error=str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/formats", methods=["POST"])
def api_formats():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Please enter a URL."}), 400
    try:
        result = list_formats(url)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not read this URL: {exc}"}), 400
    return jsonify(result)


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    option_id = (data.get("format") or "").strip()
    if not url or not option_id:
        return jsonify({"error": "Missing URL or format."}), 400

    job_id = uuid.uuid4().hex
    _set_job(job_id, status="queued", percent=0)
    thread = threading.Thread(
        target=_run_download, args=(job_id, url, option_id), daemon=True
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def api_progress(job_id: str):
    job = _get_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown job."}), 404
    return jsonify(job)


@app.route("/api/downloads-dir")
def api_downloads_dir():
    return jsonify({"path": str(get_downloads_dir())})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
