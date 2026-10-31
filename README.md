<div align="center">

# 🎬 Video-Digest

**音画双模态视频笔记与智能摘要 CLI 工具**

*A lightweight video summarizer CLI that combines audio transcripts with visual OCR.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9%2B-brightgreen.svg)](https://www.python.org/)
[![Platform: macOS / Linux / Windows](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](https://github.com/tdsmonster/video-digest)

<p align="center">
  <a href="#-核心特点">核心特点</a> •
  <a href="#-执行流程图">执行流程</a> •
  <a href="#-快速开始">快速开始</a> •
  <a href="#-使用示例">使用示例</a> •
  <a href="#-输出产物">输出产物</a> •
  <a href="README_EN.md">English Documentation</a>
</p>

---

</div>

平时看视频、公开课或技术实操分享时，单纯提取字幕往往会漏掉屏幕上的关键信息（如演示的代码、报错、PPT 要点和图表）。`Video-Digest` 在提取音频台词的同时，自动抽取关键帧画面并识别其中的文字，整理成一份图文兼备的完整笔记。

---

## 🗺️ 执行流程图

```
 ┌─────────────────┐       ┌────────────────────────┐       ┌─────────────────────────┐       ┌─────────────────────┐
 │ 1. 输入与字幕   │  ──►  │ 2. 音画双轨处理        │  ──►  │ 3. 提炼与模型归纳       │  ──►  │ 4. 双轨产出         │
 │ • 在线链接/本地 │       │ • 语音: MLX/Whisper ASR│       │ • Map-Reduce 防截断     │       │ • summary.txt (卡片)│
 │ • 原生字幕优先  │       │ • 画面: ANE/RapidOCR   │       │ • 通用 OpenAI/DeepSeek  │       │ • report.md (长笔记)│
 └─────────────────┘       └────────────────────────┘       └─────────────────────────┘       └─────────────────────┘
```

<div align="center">
  <img src="assets/workflow_pipeline.svg" alt="Video-Digest Pipeline Workflow" width="95%">
</div>

---

## ✨ 核心特点

1. **台词与画面结合**
   - **台词优先**：优先秒级提取视频的原生官方字幕；若无字幕，则回退到本地 Whisper（如 macOS MLX-Whisper、whisper.cpp 等）进行离线转写。
   - **画面佐证**：抽取代表性关键帧，通过本地 OCR（macOS 原生 Vision ANE 或跨平台的 RapidOCR）读取屏幕代码与图表文字，并合成一张 4×4 的分镜大图供快速浏览。

2. **轻量与零强制重型依赖**
   - CLI 核心逻辑基于 Python 标准库编写，开箱即用。
   - 过程中的视频、音频临时切片在处理完成后会自动删除（`finally` 保证），单次只保留文本与拼图，磁盘长期占用 `< 2MB`。

3. **支持标准大模型**
   - 支持任何 OpenAI 兼容接口（如 DeepSeek、OpenAI、本地 Ollama/vLLM 等），仅需配置标准环境变量或命令行参数。

---

## 🛠️ 环境要求

- **Python**：3.9 及以上
- **系统工具**：
  - `ffmpeg`（音视频解码与切片必备）：`brew install ffmpeg` 或 `apt install ffmpeg`
  - `yt-dlp`（在线视频下载）：`pip install yt-dlp`
- **可选依赖**：
  - Apple Silicon 用户推荐安装 `mlx-whisper`（离线转写硬件加速）：`pip install mlx-whisper`
  - 非 macOS 用户若需画面 OCR，可安装 `pip install rapidocr-onnxruntime`

---

## 🚀 快速开始

### 1. 安装

可以直接克隆仓库并在本地以可编辑模式安装：

```bash
git clone https://github.com/tdsmonster/video-digest.git
cd video-digest
pip install -e .
```

### 2. 配置 API Key

工具使用大语言模型进行最后的提炼与归纳。请先设置环境变量：

```bash
# 使用 DeepSeek (推荐，性价比极高)
export DEEPSEEK_API_KEY="your-api-key"

# 或使用 OpenAI / 其它兼容端点
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"
```

---

## 💡 使用示例

### 1. 总结在线视频或 B站视频

```bash
# 传入视频链接或 B站 BV 号
video-digest "https://www.bilibili.com/video/BV1xx411c7mD"

# 指定输出目录
video-digest "BV1xx411c7mD" -o ./my_notes
```

### 2. 分析本地视频文件

```bash
video-digest "/path/to/meeting_recording.mp4"
```

### 3. 需要登录凭证的视频（可选）

对于部分高清或仅限会员/登录观看的视频，可以传入导出的 `cookies.txt`：

```bash
video-digest "https://www.bilibili.com/video/BV..." --cookies cookies.txt
```

---

## 📂 输出产物

处理完成后，指定的输出目录下会生成以下文件：

| 文件名 | 说明 |
| :--- | :--- |
| **`summary.txt`** | 适合快速手机浏览的精简卡片（包含核心概述、时间戳关键节点、思考结论） |
| **`report.md`** | 详细的内容整理笔记 Markdown（包含时间线推进、音画互证信息与完整总结） |
| **`contact_sheet.jpg`** | 4×4 关键帧缩略拼图，方便一眼了解整段视频的视觉走势 |
| **`transcript.srt`** | 提取或识别到的完整时间轴字幕文件 |

---

## 📄 开源许可

本项目采用 [MIT License](LICENSE) 开源。欢迎提 Issue 与 PR。
