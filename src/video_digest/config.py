"""配置加载：默认值 ← config.toml ← 环境变量。

配置文件查找顺序（第一个存在的生效）：
  1. 命令行 --config / 环境变量 VIDEO_DIGEST_CONFIG
  2. 当前目录 ./video-digest.toml
  3. 用户配置目录：
     - Windows: %APPDATA%\\video-digest\\config.toml
     - 其他:    $XDG_CONFIG_HOME/video-digest/config.toml（默认 ~/.config/video-digest/config.toml）
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

APP = "video-digest"


def config_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP


def data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP


DEFAULTS: dict = {
    "output": {
        "dir": "",                 # 空 = <data_dir>/digests
        "language": "zh",          # 摘要语言：zh / en / ja / ko ...
        "keep_last": 20,           # 自动清理：最多保留多少个任务目录
        "max_gb": 2.0,
    },
    "llm": {
        # 任何 OpenAI 兼容接口都行（DeepSeek / OpenAI / OpenRouter / Ollama / vLLM ...）
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",     # DeepSeek 当前模型：deepseek-flash（快）/ deepseek-v4-pro（强）；旧名 deepseek-chat 会自动映射
        "api_key": "",             # 建议用环境变量 VIDEO_DIGEST_API_KEY，不要写进文件
        "api_key_env": "",         # 也可以指定从哪个环境变量读 key
        "max_tokens": 16000,
        "temperature": 0.2,
        "timeout": 300,
    },
    "video": {
        # 需要下载时的最高分辨率。画面小字（录屏、代码、PPT）对清晰度敏感：
        # 实测录屏教程的文字行召回 480p 79% / 720p 88% / 1080p 100%；下载量约 1x / 1.8x / 3.8x
        "max_height": 1080,
    },
    "asr": {
        # auto: Apple Silicon → mlx；有 faster-whisper → faster-whisper；有 whisper.cpp → whisper-cpp；否则 none
        "backend": "auto",         # auto | mlx | faster-whisper | whisper-cpp | none
        "model": "",               # 空 = 后端默认（mlx: whisper-large-v3-turbo；faster-whisper: GPU turbo / CPU small）
        "language": "",            # 空 = 自动检测
        "python": "",              # 用另一个 Python 解释器跑 ASR（例如单独的 mlx venv）；空 = 当前解释器
        "device": "auto",          # faster-whisper: auto | cpu | cuda
        "whisper_cpp_bin": "whisper-cli",
        "whisper_cpp_model": "",   # ggml 模型文件路径
    },
    "ocr": {
        "backend": "auto",         # auto | apple-vision | rapidocr | tesseract | none
        "frames": 48,              # 候选帧数
        "keep": 30,                # 去重后最多送给 LLM 的画面文字条数
        "tesseract_lang": "chi_sim+eng",
    },
    "bilibili": {
        "cookies": "",             # 空 = <data_dir>/bili-cookies.txt（用 `video-digest bili-login` 生成）
    },
    "watch": {
        "output_dir": "",          # 空 = <data_dir>/watch
        "papers_dir": "",          # 空 = 不缓存论文 PDF
        "state_db": "",            # 空 = <data_dir>/watch_state.db
        "max_per_run": 5,
        "mode": "auto",
        "fetch_links": True,       # 解析简介/置顶评论里的链接（arXiv / GitHub / 网页）
        "sources": [],             # [[watch.sources]] 见 config.example.toml
    },
}


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def find_config(explicit: str | None = None) -> Path | None:
    cands = [explicit, os.environ.get("VIDEO_DIGEST_CONFIG"), "video-digest.toml", str(config_dir() / "config.toml")]
    for c in cands:
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    return None


def load(explicit: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = find_config(explicit)
    if path:
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    cfg["_path"] = str(path) if path else None

    env = os.environ
    simple = {
        "VIDEO_DIGEST_BASE_URL": ("llm", "base_url"),
        "VIDEO_DIGEST_MODEL": ("llm", "model"),
        "VIDEO_DIGEST_ASR": ("asr", "backend"),
        "VIDEO_DIGEST_OCR": ("ocr", "backend"),
        "VIDEO_DIGEST_OUTPUT": ("output", "dir"),
        "VIDEO_DIGEST_LANGUAGE": ("output", "language"),
        "VIDEO_DIGEST_MAX_HEIGHT": ("video", "max_height"),
    }
    for k, (sec, key) in simple.items():
        if env.get(k):
            cfg[sec][key] = env[k]

    d = data_dir()
    cfg["output"]["dir"] = str(Path(cfg["output"]["dir"] or d / "digests").expanduser())
    cfg["bilibili"]["cookies"] = str(Path(cfg["bilibili"]["cookies"] or d / "bili-cookies.txt").expanduser())
    w = cfg["watch"]
    w["output_dir"] = str(Path(w["output_dir"] or d / "watch").expanduser())
    w["state_db"] = str(Path(w["state_db"] or d / "watch_state.db").expanduser())
    if w["papers_dir"]:
        w["papers_dir"] = str(Path(w["papers_dir"]).expanduser())
    return cfg


def api_key(cfg: dict) -> str:
    """优先级：api_key_env 指定的变量 → VIDEO_DIGEST_API_KEY → 配置文件 api_key → DEEPSEEK_API_KEY / OPENAI_API_KEY。"""
    llm = cfg["llm"]
    env = os.environ
    for name in (llm.get("api_key_env"), "VIDEO_DIGEST_API_KEY"):
        if name and env.get(name):
            return env[name]
    if llm.get("api_key"):
        return llm["api_key"]
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        if env.get(name):
            return env[name]
    return ""
