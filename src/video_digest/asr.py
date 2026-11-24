"""语音转写后端选择。

auto 的选择顺序：
  1. Apple Silicon 且能 import mlx_whisper            → mlx
  2. 能 import faster_whisper（Linux / Windows / Intel Mac，CUDA 或 CPU）→ faster-whisper
  3. PATH 里有 whisper.cpp 的 whisper-cli 且配了模型     → whisper-cpp
  4. 都没有 → none（只能靠平台字幕 / 画面文字）
"""
from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .util import log, run_cmd

WORKER = Path(__file__).with_name("asr_worker.py")


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _python(cfg) -> str:
    return cfg["asr"].get("python") or sys.executable


def _has_module(cfg, mod: str) -> bool:
    py = _python(cfg)
    if py == sys.executable:
        return importlib.util.find_spec(mod) is not None
    try:
        r = subprocess.run([py, "-c", f"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('{mod}') else 1)"],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def resolve_backend(cfg) -> str:
    b = cfg["asr"]["backend"]
    if b != "auto":
        return b
    if is_apple_silicon() and _has_module(cfg, "mlx_whisper"):
        return "mlx"
    if _has_module(cfg, "faster_whisper"):
        return "faster-whisper"
    if shutil.which(cfg["asr"]["whisper_cpp_bin"]) and cfg["asr"]["whisper_cpp_model"]:
        return "whisper-cpp"
    return "none"


def transcribe(media: Path, out_dir: Path, duration: float, cfg) -> Path | None:
    """媒体 → out_dir/transcript.srt；失败或无后端返回 None。"""
    backend = resolve_backend(cfg)
    if backend == "none":
        log(" -> 没有可用的语音转写后端（见 `video-digest doctor`）")
        return None
    srt_out = out_dir / "transcript.srt"
    wav = out_dir / "audio16k.wav"
    c, _, err = run_cmd(["ffmpeg", "-loglevel", "error", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                        timeout=int(max(60, duration * 0.2)))
    if c != 0 or not wav.exists() or wav.stat().st_size < 32_000:
        log(" -> 无可用音轨")
        return None
    tmo = int(max(600, duration * (1.5 if backend == "mlx" else 4)))
    a = cfg["asr"]
    log(f" -> 本地转写（{backend}）...")
    if backend in ("mlx", "faster-whisper"):
        cmd = [_python(cfg), str(WORKER), backend, str(wav), str(out_dir), "--device", a.get("device", "auto")]
        if a.get("model"):
            cmd += ["--model", a["model"]]
        if a.get("language"):
            cmd += ["--language", a["language"]]
        c, _, err = run_cmd(cmd, timeout=tmo)
    elif backend == "whisper-cpp":
        cmd = [a["whisper_cpp_bin"], "-m", a["whisper_cpp_model"], "-f", str(wav), "-osrt", "-of", str(out_dir / "transcript"),
               "-l", a.get("language") or "auto"]
        c, _, err = run_cmd(cmd, timeout=tmo)
    else:
        log(f" -> 未知 ASR 后端: {backend}")
        return None
    wav.unlink(missing_ok=True)
    if c != 0 or not srt_out.exists():
        log(f" -> 转写无输出 (code {c}): {(err or '').strip()[-200:]}")
        return None
    return srt_out
