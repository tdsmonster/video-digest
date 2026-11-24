<div align="center">

<img src="assets/banner-en.svg" alt="video-digest" width="100%">

<p>
  <a href="https://github.com/tdsmonster/video-digest/actions/workflows/ci.yml"><img src="https://github.com/tdsmonster/video-digest/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/tdsmonster/video-digest/releases"><img src="https://img.shields.io/github/v/release/tdsmonster/video-digest?color=8b5cf6" alt="Release"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776ab" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-0ea5e9" alt="Platforms">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e" alt="MIT"></a>
</p>

**Paste a video link, get back a timestamped summary card. Every point is sourced, and nothing is written without evidence.**

[Quick start](#-quick-start) · [Usage](#-usage) · [Watch](#-watch-subscriptions) · [Configuration](#-configuration) · [中文](README.md)

</div>

<br>

<p align="center">
  <img src="assets/demo-card.svg" alt="Example output: summary card for a 19-minute paper walkthrough" width="88%">
</p>

<p align="center"><sub>Real output (Chinese, the default language; set <code>--language en</code> for English). A 19-minute Bilibili paper walkthrough summarized from its AI subtitles, with no download.</sub></p>

## ✨ Highlights

<table>
<tr>
<td width="50%" valign="top">

### 🧾 No evidence, no summary
If there is no reliable speech, on-screen text, description or danmaku, it fails with exit code `2`. It never writes a plausible-looking summary from the title alone.

</td>
<td width="50%" valign="top">

### 🎵 Catches Whisper hallucinations
Whisper "hears" lyric credits, "thanks for watching" or "oh oh oh" over music and silence. These are filtered out by text density, repetition and phrase checks.

</td>
</tr>
<tr>
<td valign="top">

### 🖼️ Reads the screen
Frames are sampled, OCR'd and de-duplicated. Slides, code, error messages and on-screen URLs become `[mm:ss]` evidence.

</td>
<td valign="top">

### ⚡ Downloads only when needed
With platform subtitles (including Bilibili AI subtitles after login) it works online. A 44-minute lecture takes about 50 seconds.

</td>
</tr>
<tr>
<td valign="top">

### 📺 Bilibili quirks handled
HTTP 567, b23.tv short links, app share text, multi-part videos, AI subtitles and danmaku peaks (passed to the LLM as audience reaction, not as video content).

</td>
<td valign="top">

### 📡 Watch subscriptions
Polls Bilibili favorites, uploaders or YouTube channels and digests new videos. It also reads the arXiv papers and GitHub repos linked in the description and pinned comment.

</td>
</tr>
</table>

## 🔍 How it works

<p align="center">
  <img src="assets/pipeline-en.svg" alt="Pipeline" width="100%">
</p>

Each video gets its own output directory:

| File | Contents |
|---|---|
| `summary.txt` | Plain-text card for Telegram, Slack and similar apps: a one-line gist, 3–5 timestamped points and a "worth watching?" verdict |
| `report.md` | Chronological long-form report: key facts, what the on-screen text added and where the evidence is weak |
| `contact_sheet.jpg` | 4×4 storyboard |
| `transcript.srt` · `ocr.txt` · `result.json` | Raw evidence and structured results |

## 🚀 Quick start

**1. Install ffmpeg**

```bash
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Ubuntu / Debian
winget install Gyan.FFmpeg     # Windows
```

**2. Install video-digest** ([uv](https://docs.astral.sh/uv/) recommended; pipx works too)

```bash
uv tool install "video-digest[all] @ git+https://github.com/tdsmonster/video-digest"
```

`[all]` picks backends for your platform: MLX-Whisper + Apple Vision on Apple Silicon, faster-whisper + RapidOCR elsewhere.

**3. Set an API key and check your setup**

```bash
export VIDEO_DIGEST_API_KEY=sk-...      # PowerShell: $env:VIDEO_DIGEST_API_KEY="sk-..."
video-digest doctor --ping
```

DeepSeek is the default, but any OpenAI-compatible endpoint works, including a local Ollama with no key. See [Configuration](#-configuration).

<details>
<summary>Installing individual backends</summary>

| extra | What it installs | For |
|---|---|---|
| `[mlx]` | MLX-Whisper | Apple Silicon |
| `[whisper]` | faster-whisper (uses CUDA on NVIDIA GPUs, falls back to CPU if the CUDA runtime is missing) | Linux / Windows / Intel Mac |
| `[ocr]` | RapidOCR | Linux / Windows |
| `[bili]` | Shows the Bilibili login QR code in the terminal | Any platform |

On macOS the Apple Vision OCR helper is compiled on first use. This needs the Xcode Command Line Tools (`xcode-select --install`).

</details>

## 🧭 Usage

```bash
video-digest https://www.youtube.com/watch?v=xxxx --language en
video-digest BV1xx411c7mD                       # Bilibili id
video-digest "【标题】 https://b23.tv/xxxx"       # app share text, pasted as is
video-digest ./lecture.mp4                      # local file
video-digest <url> --json                       # for scripts and bots
```

| `--mode` | Behavior |
|---|---|
| `auto` (default) | Use platform subtitles online if available, otherwise download |
| `online` | Subtitles, description and danmaku only; never downloads |
| `download` | Always download, transcribe and OCR |

Exit codes: `0` ok · `1` unavailable · `2` insufficient evidence · `3` LLM failure (a fallback report is still written)

<details>
<summary>Resolution: why downloads default to 1080p</summary>

On-screen text recognition is sensitive to resolution. On a 5-minute screen-recording tutorial (24 identical frames, 1080p as reference), line-level OCR recall was **79% at 480p**, **88% at 720p** and **100% at 1080p**, for roughly 1× / 1.8× / 3.8× the download size. Large titles survive at 480p; small UI text and prices do not. Speech recognition is unaffected because audio always uses the best stream. `auto` mode skips the download entirely when subtitles exist. To save bandwidth, use `--max-height 720` or set `[video] max_height = 720`.

</details>

`video-digest bili-login` logs in to Bilibili by QR code, which unlocks AI subtitles and private favorites. The cookie stays local with mode 600.

## 📡 Watch subscriptions

Add sources to your config (see [config.example.toml](config.example.toml)):

```toml
[[watch.sources]]
type = "ytdlp"                   # any channel / playlist yt-dlp supports
url = "https://www.youtube.com/@SomeChannel/videos"
label = "YouTube · SomeChannel"

[[watch.sources]]
type = "bili_fav"                # Bilibili favorites; bili_up for uploaders
id = 1234567890
label = "Favorites · AI"
```

```bash
video-digest watch --init        # first run: record existing videos as baseline
video-digest watch               # run from cron / Task Scheduler
```

stdout is a digest you can forward to Telegram or email. It stays empty when there is nothing new. Full per-video reports go to `watch.output_dir`.

## 🔧 Configuration

`video-digest config-path` shows where the config file lives (`~/.config/video-digest/config.toml`, or `%APPDATA%\video-digest\config.toml` on Windows). Every key is optional.

| Goal | Setting |
|---|---|
| OpenAI | `[llm]` `base_url = "https://api.openai.com/v1"` · `model = "gpt-4.1-mini"` |
| Local Ollama | `[llm]` `base_url = "http://localhost:11434/v1"` · `model = "qwen3:14b"` |
| English output | `[output]` `language = "en"` or `--language en` |
| Specific ASR backend | `[asr]` `backend = "faster-whisper"` · `device = "cuda"` |
| whisper.cpp | `[asr]` `backend = "whisper-cpp"` · `whisper_cpp_model = "/path/ggml-large-v3-turbo.bin"` |

Environment variables: `VIDEO_DIGEST_API_KEY`, `VIDEO_DIGEST_BASE_URL`, `VIDEO_DIGEST_MODEL`, `VIDEO_DIGEST_ASR`, `VIDEO_DIGEST_OCR`, `VIDEO_DIGEST_OUTPUT`, `VIDEO_DIGEST_LANGUAGE`, `VIDEO_DIGEST_CONFIG`.

## 🖥️ Platforms

| Platform | Speech-to-text | On-screen text |
|---|---|---|
| 🍎 macOS · Apple Silicon | MLX-Whisper large-v3-turbo | Apple Vision (built in) |
| 🍎 macOS · Intel | faster-whisper (CPU) | Apple Vision |
| 🐧 Linux / 🪟 Windows + NVIDIA | faster-whisper large-v3-turbo (CUDA) | RapidOCR |
| 🐧 Linux / 🪟 Windows · CPU only | faster-whisper small (int8) | RapidOCR |

Tested on macOS, Linux (Docker) and a real Windows laptop; CI runs on all three. With no backends installed, videos that have platform subtitles still work.

## 🔒 Privacy

- Media is processed locally and deleted afterwards.
- Only text (transcript, OCR text, title, description, danmaku stats) is sent to the LLM.
- `watch` link enrichment calls arXiv, the GitHub API and [Jina Reader](https://jina.ai/reader). Set `fetch_links = false` to turn this off.

## 🛠️ Development

```bash
git clone https://github.com/tdsmonster/video-digest && cd video-digest
uv venv && uv pip install -e ".[dev,all]"
pytest
```

Issues and PRs are welcome.

## 📄 License

[MIT](LICENSE)
