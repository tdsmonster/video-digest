# video-digest

一个轻量、客观的音画双模态视频总结小工具。

平时看视频、公开课或技术实操分享时，单纯提取字幕往往会漏掉屏幕上的关键信息（如演示的代码、报错、PPT 要点和图表）。`video-digest` 尝试在提取音频台词的同时，抽取关键帧画面并识别其中的文字，整理成一份更完整的图文笔记。

---

## 🌟 核心特点

1. **台词与画面结合**：
   - 优先提取视频的原生官方字幕；若无官方字幕，可回退到本地 Whisper（如 macOS MLX-Whisper、whisper.cpp 等）进行离线转写。
   - 抽取代表性关键帧，通过本地 OCR（macOS 原生 Vision ANE 或跨平台的 RapidOCR）读取屏幕代码与图表文字，并合成一张 4×4 的分镜大图供快速浏览。
2. **轻量与零强制重型依赖**：
   - CLI 核心逻辑基于 Python 标准库编写，开箱即用。
   - 过程中的视频、音频临时切片在处理完成后会自动删除，单次产物只保留文本与拼图，不堆积硬盘垃圾。
3. **支持标准大模型**：
   - 支持任何 OpenAI 兼容接口（如 DeepSeek、OpenAI、本地 Ollama/vLLM 等），仅需配置标准环境变量或命令行参数。

---

## 🛠️ 环境要求

- **Python** 3.9+
- **系统依赖**：
  - `ffmpeg`（音视频解码与切片必备）：`brew install ffmpeg` 或 `apt install ffmpeg`
  - `yt-dlp`（在线视频下载）：`pip install yt-dlp`
- **可选依赖**：
  - Apple Silicon 机器推荐安装 `mlx-whisper`（离线转写加速）：`pip install mlx-whisper`
  - 非 macOS 机器若需画面 OCR，可安装 `pip install rapidocr-onnxruntime`

---

## 📦 安装与配置

可以直接克隆仓库并在本地以可编辑模式安装：

```bash
git clone https://github.com/tdsmonster/video-digest.git
cd video-digest
pip install -e .
```

也可以直接将脚本所在目录加入环境变量，或直接运行：

```bash
python3 -m video_digest.cli --help
```

### 配置 API Key

工具使用大语言模型进行最后的提炼与归纳。请先设置环境变量：

```bash
# 使用 DeepSeek
export DEEPSEEK_API_KEY="your-api-key"

# 或使用 OpenAI / 其它兼容端点
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"
```

---

## 🚀 快速使用

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

## 📂 输出内容

处理完成后，指定的输出目录下会生成以下文件：

- `summary.txt`：适合快速手机浏览的精简卡片（包含核心概述、时间戳关键节点、思考结论）。
- `report.md`：详细的内容整理笔记 Markdown（包含时间线推进、音画互证信息与完整总结）。
- `contact_sheet.jpg`：4×4 关键帧缩略拼图，方便一眼了解整段视频的视觉走势。
- `transcript.srt`：提取或识别到的完整时间轴字幕文件。

---

## 📄 License

MIT License.
