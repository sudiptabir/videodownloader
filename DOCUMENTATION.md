# Video Downloader — How It Works

A general-purpose desktop video downloader. You paste a video URL into a local
web page, the app reads the available qualities, and when you pick one it
downloads the video as an MP4 (or audio as MP3) into your system's `Downloads`
folder.

> **Responsible use.** This is a neutral tool. Only download content you are
> authorized to download — your own uploads, public-domain or Creative-Commons
> material, licensed content, or platforms whose Terms of Service permit
> downloading. You are responsible for complying with copyright law and each
> site's Terms of Service.

---

## 1. The big picture

The app is a small **local web application**. Nothing is hosted on the internet;
everything runs on your own machine at `http://127.0.0.1:5000`.

There are three moving parts:

| Part | Technology | Role |
|------|-----------|------|
| **UI** | HTML + CSS + JavaScript (`templates/index.html`) | The page you interact with in the browser |
| **Backend** | Python + Flask (`app.py`) | Receives requests from the page, drives the download engine |
| **Engine** | `yt-dlp` + `ffmpeg` | Extracts video info, downloads streams, merges/converts to MP4/MP3 |

The flow at a glance:

```
Browser (index.html)
      │  HTTP (JSON) on 127.0.0.1:5000
      ▼
Flask backend (app.py)
      │  Python calls
      ▼
yt-dlp  ──uses──►  ffmpeg.exe / ffprobe.exe  (bundled in bin/)
      │
      ▼
MP4 / MP3 saved to  C:\Users\<you>\Downloads
```

---

## 2. Starting the app

The entry point is `run.py`:

1. It imports the Flask `app` from `app.py`.
2. It prints a banner to the console showing the local URL.
3. After a short delay it **opens your default browser** to
   `http://127.0.0.1:5000`.
4. It serves the app using **waitress** (a production-grade WSGI server). If
   waitress isn't installed, it falls back to Flask's built-in development
   server.

As long as the console window stays open, the server keeps running. Closing the
window stops the app.

---

## 3. What happens when you use it

### Step A — You paste a URL and click "Fetch"

The page sends a `POST /api/formats` request with the URL. On the backend,
`list_formats(url)` runs:

1. It calls `yt_dlp.YoutubeDL(...).extract_info(url, download=False)`. This
   **probes** the URL without downloading anything — it returns metadata: the
   title, uploader, thumbnail, duration, and a long list of available
   **formats** (each format is one downloadable stream).
2. yt-dlp typically returns many formats: separate video-only streams at
   different resolutions, separate audio-only streams, and sometimes combined
   streams. The raw list is messy and confusing to a user.
3. `list_formats` **simplifies** this into clean choices:
   - It groups video streams by **height** (e.g. 1080p, 720p, 480p), keeping the
     best variant at each height.
   - It adds a high-fps marker (e.g. "1080p 60fps") when the stream runs at
     50fps or higher.
   - If any audio-only stream exists, it adds a single **"Audio only (MP3)"**
     option.
4. It returns the title, thumbnail, uploader, and this tidy list of quality
   options to the page.

The page then renders the video's title/thumbnail and a grid of quality buttons.

### Step B — You pick a quality and click "Download"

The page sends a `POST /api/download` request with the URL and the chosen option
id (e.g. `video-720` or `audio`). The backend:

1. Creates a unique **job id** and registers it in an in-memory job table with
   status `queued`.
2. Starts the actual download on a **background thread** (so the web request
   returns immediately and the UI stays responsive).
3. Returns the `job_id` to the page.

The download itself (`_run_download`) does the following:

1. **Picks a yt-dlp format string** based on your choice
   (`_format_selector`):
   - For video: `bestvideo[height<=CHOSEN]+bestaudio / best[height<=CHOSEN] / best`.
     In plain terms: "get the best video at or below the height I picked, plus
     the best audio, and merge them; if that's not possible, fall back to the
     best combined stream."
   - For audio: `bestaudio/best`.
2. **Sets the output path** to your Downloads folder, using the video's title as
   the filename (`%(title)s.%(ext)s`), with Windows-safe filename rules.
3. **Reports progress** via a hook. As bytes arrive, it updates the job table
   with a percentage, speed, and ETA. When a stream finishes downloading, the
   status becomes `processing` (merge/convert is happening).
4. **Post-processes with ffmpeg**:
   - Video → merges the separate video and audio streams and ensures the final
     container is **MP4** (remux, so it's fast and lossless when possible).
   - Audio → extracts and converts to **MP3** at 192 kbps.
5. On success it records the final file path and sets status `done`. On any
   error it records the error message and sets status `error`.

### Step C — The page shows live progress

After starting a download, the page **polls** `GET /api/progress/<job_id>` every
~0.6 seconds and updates the progress bar and status text:

- `downloading` → shows percent and speed.
- `processing` → shows "Converting to MP4…".
- `done` → shows the saved filename in green.
- `error` → shows the error message in red.

---

## 4. The HTTP API (reference)

All endpoints are served from `http://127.0.0.1:5000`.

| Method & path | Purpose | Request body | Response |
|---------------|---------|--------------|----------|
| `GET /` | Serves the web UI | — | `index.html` |
| `POST /api/formats` | List qualities for a URL | `{"url": "..."}` | `{title, thumbnail, duration, uploader, formats: [...]}` |
| `POST /api/download` | Start a download job | `{"url": "...", "format": "video-720"}` | `{"job_id": "..."}` |
| `GET /api/progress/<job_id>` | Poll a job's progress | — | `{status, percent, speed?, eta?, filename?, error?}` |
| `GET /api/downloads-dir` | Where files are saved | — | `{"path": "C:\\Users\\...\\Downloads"}` |

**Format option object** (inside `formats`):

```json
{
  "id": "video-720",        // sent back when downloading
  "label": "720p",          // shown on the button
  "type": "video",          // "video" or "audio"
  "height": 720,
  "ext": "mp4",
  "filesize": "45.2 MB"     // estimate, may be empty
}
```

**Job status object** (`/api/progress`):

```json
{
  "status": "downloading",  // queued | downloading | processing | done | error
  "percent": 42.5,
  "speed": 1048576,          // bytes/sec, when downloading
  "eta": 30,                 // seconds, when known
  "filename": "My Video.mp4",// when done
  "filepath": "C:\\Users\\...\\Downloads\\My Video.mp4",
  "error": "..."             // when error
}
```

---

## 5. How the pieces fit in the code (`app.py`)

- **`_base_dir()` / `get_ffmpeg_location()`** — Locate the bundled
  `ffmpeg.exe`/`ffprobe.exe`. This works both when running from source and when
  running as a packaged executable (PyInstaller sets `sys.frozen` and
  `sys._MEIPASS`). If ffmpeg is found in `bin/`, its path is passed to yt-dlp via
  `ffmpeg_location` so the app never depends on a system-wide ffmpeg install.
- **`get_downloads_dir()`** — Returns `~/Downloads`, creating it if needed.
- **`JOBS` + `_set_job` / `_get_job`** — A thread-safe, in-memory table tracking
  each download job's progress. (It is not persisted; restarting the app clears
  it.)
- **`_human_size()`** — Formats byte counts as human-readable sizes (MB, GB…).
- **`list_formats()`** — Probes a URL and builds the simplified quality list
  (Step A above).
- **`_format_selector()`** — Maps a UI option id to a yt-dlp format string and a
  flag for audio-only.
- **`_run_download()`** — Runs the actual download + ffmpeg post-processing on a
  background thread and updates the job table (Step B above).
- **Flask routes** — Thin wrappers that validate input and call the functions
  above.

---

## 6. Why ffmpeg is bundled

Modern sites usually serve the best video and the best audio as **separate
streams**. To produce a single playable MP4, those streams must be **merged**,
and that is what ffmpeg does. ffmpeg is also what converts audio to MP3.

To avoid asking users to install ffmpeg separately, the project ships
`ffmpeg.exe` and `ffprobe.exe` inside the `bin/` folder, and the app points
yt-dlp at them. This makes the app self-contained.

---

## 7. Project layout

```
videodownloader/
├─ app.py                 # Flask backend + download logic (the core)
├─ run.py                 # Launcher: starts server, opens browser
├─ templates/
│  └─ index.html          # The web UI (HTML/CSS/JS in one file)
├─ bin/
│  ├─ ffmpeg.exe          # Bundled media merger/converter
│  └─ ffprobe.exe         # Bundled media inspector
├─ requirements.txt       # Python dependencies (flask, yt-dlp, waitress)
├─ VideoDownloader.spec   # PyInstaller packaging config
├─ build/  dist/          # Generated by PyInstaller (the .exe lives in dist/)
└─ DOCUMENTATION.md       # This file
```

---

## 8. Running it

### From Python (recommended on this machine)

```powershell
cd C:\Users\HP\Downloads\videodownloader
python run.py
```

Your browser opens to `http://127.0.0.1:5000`. Keep the console window open
while using the app.

> On this machine, **Smart App Control** blocks the unsigned packaged `.exe`.
> Running through Python (which is trusted) is the reliable way to launch the
> app. Smart App Control is a one-way setting, so it's not worth disabling just
> to run a personal tool.

### From the packaged executable (where policy allows)

If the machine's security policy permits unsigned apps, double-click:

```
dist\VideoDownloader\VideoDownloader.exe
```

It behaves identically — console banner, browser opens, ffmpeg bundled inside.

---

## 9. Design notes & limitations

- **Local only.** The server binds to `127.0.0.1`, so it is reachable only from
  your own machine, not the network.
- **One job table, in memory.** Progress is tracked in RAM and cleared on
  restart. There is no download history or resume-across-restart.
- **MP4 / MP3 output.** Video always ends up as MP4; the audio-only option
  produces MP3 at 192 kbps.
- **Filename = video title.** Files are named after the source title using
  Windows-safe characters. Re-downloading the same video overwrites the previous
  file unless the title differs.
- **Errors surface in the UI.** If yt-dlp can't read a URL or a download fails,
  the reason is shown in the page (red status) and logged to the job.
- **No authentication / no DRM.** The app cannot download content behind logins
  it isn't given access to, and it cannot bypass DRM. That is by design.
