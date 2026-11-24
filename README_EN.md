# video-digest

**Video → subtitles/transcript + on-screen text → evidence-grounded AI summary.** Bilibili, YouTube (anything yt-dlp supports) and local files. Runs on macOS, Linux and Windows.

[中文](README.md)

For each video you get:

- `summary.txt`: a plain-text card for chat apps. It has a one-line gist, 3–5 key points with `[mm:ss]` timestamps and a "worth watching?" verdict.
- `report.md`: a chronological long-form report. It lists key facts, what the on-screen text added and where the evidence is weak.
- `contact_sheet.jpg`: a 4×4 storyboard.
- `transcript.srt` / `ocr.txt` / `result.json`: the raw evidence and structured results.

## Why not just "subtitles → LLM"

- **It doesn't make things up.** If there is no reliable speech, on-screen text, description or danmaku, it exits with code 2 instead of writing a summary from the title alone.
- **It catches music hallucinations.** Whisper "hears" lyric credits, "thanks for watching" or "oh oh oh" over music and silence. A density/repetition/phrase gate drops these instead of treating them as dialogue.
- **It reads the screen.** Frames are sampled, OCR'd and de-duplicated, so slides, code, error messages and URLs become timestamped evidence.
- **It downloads only when needed.** With platform subtitles (including Bilibili AI subtitles after login), a 44-minute lecture is summarized in about 50 seconds with no download. Otherwise it fetches ≤480p video and transcribes locally.
- **It handles Bilibili quirks.** These include HTTP 567 (yt-dlp #17847), b23.tv short links, app share text and multi-part videos. Danmaku peaks go to the LLM as audience reaction, not as video content.
- **It can watch subscriptions.** `video-digest watch` polls Bilibili favorites, uploaders or any YouTube channel/playlist and digests new videos. It also follows arXiv and GitHub links from the description, pinned comment or screen.

## Install

First install **ffmpeg**: `brew install ffmpeg` / `sudo apt install ffmpeg` / `winget install Gyan.FFmpeg`.

```bash
# Everything: MLX + Apple Vision on Apple Silicon; faster-whisper + RapidOCR elsewhere
uv tool install "video-digest[all] @ git+https://github.com/tdsmonster/video-digest"
# or: pipx install "video-digest[all] @ git+https://github.com/tdsmonster/video-digest"

export VIDEO_DIGEST_API_KEY=sk-...     # PowerShell: $env:VIDEO_DIGEST_API_KEY="sk-..."
video-digest doctor --ping
```

Extras you can install on their own:

- `[whisper]`: faster-whisper, used automatically with CUDA if there is an NVIDIA GPU
- `[mlx]`: MLX-Whisper for Apple Silicon
- `[ocr]`: RapidOCR
- `[bili]`: shows the Bilibili login QR code in the terminal

On macOS the Apple Vision OCR helper is compiled on first use. This needs the Xcode Command Line Tools.

## Usage

```bash
video-digest https://www.youtube.com/watch?v=xxxx --language en
video-digest BV1xx411c7mD
video-digest ./lecture.mp4
video-digest <url> --mode download    # force download + transcription + OCR
video-digest <url> --json
```

`--mode` options:

- `auto` (default): use subtitles online if available, otherwise download
- `online`: never download
- `download`: always download

Exit codes: `0` ok, `1` unavailable, `2` insufficient evidence, `3` LLM failure.

`video-digest bili-login` logs in to Bilibili by QR code. The cookie stays local with mode 600.

## Watch subscriptions

Add sources to `config.toml` (see [config.example.toml](config.example.toml)). Supported types are `bili_fav`, `bili_up` and `ytdlp` (any channel or playlist URL).

```bash
video-digest watch --init   # first run: record existing videos as baseline
video-digest watch          # from cron / Task Scheduler; prints a digest only when there is something new
```

## Configuration

Run `video-digest config-path` to find the config file. Any OpenAI-compatible endpoint works:

```toml
[llm]
base_url = "https://api.openai.com/v1"   # or http://localhost:11434/v1 for Ollama (no key needed)
model = "gpt-4.1-mini"

[output]
language = "en"
```

| Platform | Speech-to-text | On-screen text |
|---|---|---|
| macOS Apple Silicon | MLX-Whisper large-v3-turbo | Apple Vision |
| macOS Intel | faster-whisper (CPU) | Apple Vision |
| Linux / Windows + NVIDIA | faster-whisper large-v3-turbo (CUDA) | RapidOCR |
| Linux / Windows CPU only | faster-whisper small (int8) | RapidOCR |

## Privacy

- Media is processed locally and deleted afterwards.
- Only text (transcript, OCR text, title, description, danmaku stats) is sent to the LLM.
- `watch` link enrichment calls arXiv, the GitHub API and Jina Reader. Set `fetch_links = false` to turn this off.

## License

MIT
