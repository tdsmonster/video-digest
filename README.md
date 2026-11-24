# video-digest

**视频 → 字幕/转写 + 画面文字 → 有证据的 AI 摘要。** 支持 B站、YouTube 和本地文件，可在 macOS、Linux、Windows 上运行。

[English](README_EN.md)

[![CI](https://github.com/tdsmonster/video-digest/actions/workflows/ci.yml/badge.svg)](https://github.com/tdsmonster/video-digest/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)

发一个链接，输出：

- `summary.txt`：适合发到聊天软件的纯文本卡片，包含一句话概括、3–5 条带 `[mm:ss]` 的要点，以及"值不值得看"
- `report.md`：按时间顺序的长报告，写明关键数据、画面文字补充了什么，以及哪些地方证据不足
- `contact_sheet.jpg`：4×4 分镜图
- `transcript.srt` / `ocr.txt` / `result.json`：原始证据和结构化结果

## 和"把字幕丢给 LLM"有什么不同

- **不编**：没有可靠台词、画面文字、简介或弹幕时，直接以退出码 2 失败，不会只凭标题生成一篇"摘要"。
- **识别音乐幻觉**：Whisper 在音乐或静音段落上常会"听出"「作曲 李宗盛」「谢谢观看」「oh oh oh」。这里按文字密度、重复次数和固定短语判断，过滤掉这类内容，不把它当成台词。
- **看画面**：均匀抽帧做 OCR 并去重，PPT、代码、报错、屏幕上的网址都会成为带时间戳的证据。
- **能不下载就不下载**：有平台字幕（包括登录后的 B站 AI 字幕）时直接在线解析，44 分钟的讲座约 50 秒出结果；没有字幕再下载 ≤480p 的视频，在本地转写（M 系列 Mac 上 18 分钟视频全流程约 3 分钟）。
- **B站细节**：处理 HTTP 567（yt-dlp #17847）、b23 短链、App 分享文案、分 P；把弹幕高能时段作为"观众反应"提供给 LLM（不当作视频内容）。
- **订阅追更**：`video-digest watch` 定时检查 B站收藏夹、UP 主或 YouTube 频道。新视频自动解析，并顺着简介、置顶评论和画面里的 arXiv、GitHub 链接一起解读。

## 流程

```
链接 / BV号 / 本地文件
  │
  ├─ 1. yt-dlp 元数据 + 平台字幕 + 弹幕
  │        有字幕 ──(auto 模式)──► 跳过下载
  ├─ 2. 下载 ≤480p → 语音转写（MLX / faster-whisper / whisper.cpp）→ 幻觉闸门
  ├─ 3. 抽帧 → OCR（Apple Vision / RapidOCR / Tesseract）→ 去重，带时间戳
  ├─ 证据闸门：什么都没有就失败，不编
  └─ 4. LLM（任何 OpenAI 兼容接口）→ summary.txt + report.md
```

## 安装

先装好 **ffmpeg**：

| 系统 | 命令 |
|---|---|
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg` |

再装 video-digest（推荐用 [uv](https://docs.astral.sh/uv/) 或 pipx）：

```bash
# 一次装齐：Apple Silicon 自动用 MLX 转写 + Apple Vision OCR；其他平台装 faster-whisper + RapidOCR
uv tool install "video-digest[all] @ git+https://github.com/tdsmonster/video-digest"
# 或 pipx install "video-digest[all] @ git+https://github.com/tdsmonster/video-digest"
```

按需单独装：`[whisper]` faster-whisper（Linux/Windows/Intel Mac，有 NVIDIA 显卡时自动用 CUDA）、`[mlx]` MLX-Whisper（Apple Silicon）、`[ocr]` RapidOCR、`[bili]` 在终端显示 B站登录二维码。

设置 LLM 的 API key（默认用 DeepSeek，也可以换成任何 OpenAI 兼容接口，见下文）：

```bash
export VIDEO_DIGEST_API_KEY=sk-...          # Windows PowerShell: $env:VIDEO_DIGEST_API_KEY="sk-..."
video-digest doctor --ping                  # 检查所有依赖并实际调用一次 LLM
```

macOS 首次使用 Apple Vision OCR 时会自动编译一个小工具，需要 Xcode Command Line Tools（`xcode-select --install`）。

## 使用

```bash
video-digest https://www.bilibili.com/video/BV1xx411c7mD
video-digest BV1xx411c7mD                     # 直接写 BV 号
video-digest "【标题】 https://b23.tv/xxxx"     # App 分享文案原样粘贴
video-digest https://youtu.be/xxxx --language en
video-digest ./lecture.mp4                    # 本地文件
video-digest <链接> --mode download            # 强制下载：转写 + 画面文字（字幕不够用时）
video-digest <链接> --json                     # 给脚本 / 机器人用
```

`--mode` 有三种：

- `auto`（默认）：有字幕时在线解析，没有就下载
- `online`：只用字幕、简介、弹幕，绝不下载
- `download`：一定下载并做转写和 OCR

退出码：`0` 成功、`1` 视频不可用、`2` 证据不足、`3` LLM 失败（已写降级报告）。

### B站登录（可选）

登录后可以拿到 B站 AI 字幕（大多数视频不需要下载），也能读取私密收藏夹：

```bash
video-digest bili-login          # 终端显示二维码，用 B站 App 扫码
video-digest bili-login --check
```

cookie 只保存在本机（权限 600），不会上传到任何地方。

### 订阅追更

在 `config.toml` 里加入来源（完整示例见 [config.example.toml](config.example.toml)）：

```toml
[[watch.sources]]
type = "bili_fav"
id = 1234567890
label = "收藏夹 · AI"

[[watch.sources]]
type = "ytdlp"
url = "https://www.youtube.com/@SomeChannel/videos"
label = "YouTube · SomeChannel"
```

```bash
video-digest watch --init     # 第一次：把现有视频登记为基线，不解析
video-digest watch            # 之后定时跑：只解析新视频；没有新内容时 stdout 为空
```

stdout 是一份汇总，适合交给 cron 转发到 Telegram 或邮件；每个视频的完整报告写在 `watch.output_dir`。

定时运行的例子：

```bash
# Linux / macOS crontab：每天 10 点和 18 点
0 10,18 * * * /path/to/video-digest watch >> ~/video-digest-watch.log 2>&1
```

```powershell
# Windows 任务计划程序
schtasks /Create /SC DAILY /ST 10:00 /TN video-digest-watch /TR "video-digest watch"
```

## 配置

`video-digest config-path` 会显示配置文件位置。复制 [config.example.toml](config.example.toml) 到该位置后修改。常用项：

| 想要 | 配置 |
|---|---|
| 用 OpenAI | `[llm] base_url="https://api.openai.com/v1"`、`model="gpt-4.1-mini"` |
| 用本地 Ollama（不需要 key） | `[llm] base_url="http://localhost:11434/v1"`、`model="qwen3:14b"` |
| 英文摘要 | `[output] language="en"` 或 `--language en` |
| 指定转写后端 | `[asr] backend="faster-whisper"`、`device="cuda"` |
| 用 whisper.cpp | `[asr] backend="whisper-cpp"`、`whisper_cpp_model="/path/ggml-large-v3-turbo.bin"` |

环境变量：`VIDEO_DIGEST_API_KEY`、`VIDEO_DIGEST_BASE_URL`、`VIDEO_DIGEST_MODEL`、`VIDEO_DIGEST_ASR`、`VIDEO_DIGEST_OCR`、`VIDEO_DIGEST_OUTPUT`、`VIDEO_DIGEST_LANGUAGE`、`VIDEO_DIGEST_CONFIG`。

## 各平台后端

| | 语音转写 | 画面文字 |
|---|---|---|
| macOS Apple Silicon | MLX-Whisper large-v3-turbo | Apple Vision（系统自带） |
| macOS Intel | faster-whisper（CPU） | Apple Vision |
| Linux / Windows + NVIDIA | faster-whisper large-v3-turbo（CUDA） | RapidOCR |
| Linux / Windows 仅 CPU | faster-whisper small（int8） | RapidOCR |

都没装也能用：有平台字幕的视频照样能出摘要，只是没字幕时无法转写。

## 隐私

- 视频、音频、截图都在本地处理，处理完立即删除。
- 发给 LLM 的只有文字：字幕或转写、OCR 文字、标题、简介、弹幕统计。
- `watch` 解读链接时会请求 arXiv、GitHub API 和 [Jina Reader](https://jina.ai/reader)（抓普通网页正文）。不需要的话设 `fetch_links = false`。

## 开发

```bash
git clone https://github.com/tdsmonster/video-digest && cd video-digest
uv venv && uv pip install -e ".[dev,all]"
pytest
```

## License

MIT
