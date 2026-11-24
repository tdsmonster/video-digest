"""画面文字识别：抽帧 + OCR 后端 + 去重。

auto 的选择顺序：
  macOS 且有 swiftc（或已编译好）→ apple-vision
  能 import rapidocr_onnxruntime（Linux / Windows / 任意平台）→ rapidocr
  PATH 里有 tesseract → tesseract
  否则 none（只用语音和简介）
"""
from __future__ import annotations

import importlib.util
import os
import json
import shutil
import sys
from pathlib import Path

from .config import data_dir
from .util import fmt_ts, log, run_cmd

SWIFT_SRC = Path(__file__).parent / "native" / "vision_ocr.swift"
NOISE = ["bilibili", "哔哩哔哩", "关注", "投币", "点赞", "一键三连", "订阅", "subscribe"]


def vision_bin() -> Path:
    return data_dir() / "bin" / "vision-ocr"


def ensure_vision_bin() -> Path | None:
    """首次使用时编译 Swift 源码；源码更新后自动重编。"""
    if sys.platform != "darwin":
        return None
    b = vision_bin()
    if b.exists() and b.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return b
    if not shutil.which("swiftc"):
        return None
    b.parent.mkdir(parents=True, exist_ok=True)
    log(" -> 首次使用，编译 Apple Vision OCR 小工具（约 10-30 秒）...")
    # 先编到临时文件再原子替换：多个进程同时首次运行时，不会有人执行到写了一半的二进制
    tmp = b.with_name(f"{b.name}.{os.getpid()}.tmp")
    c, _, err = run_cmd(["swiftc", "-O", str(SWIFT_SRC), "-o", str(tmp)], timeout=300)
    if c != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        log(f" -> [警告] swiftc 编译失败: {err.strip()[-200:]}")
        return None
    os.replace(tmp, b)
    return b


def resolve_backend(cfg) -> str:
    b = cfg["ocr"]["backend"]
    if b != "auto":
        return b
    if sys.platform == "darwin" and (vision_bin().exists() or shutil.which("swiftc")):
        return "apple-vision"
    if importlib.util.find_spec("rapidocr_onnxruntime"):
        return "rapidocr"
    if shutil.which("tesseract"):
        return "tesseract"
    return "none"


def _ocr_frames(frames: list[Path], backend: str, cfg) -> tuple[dict[int, str], str]:
    raw: dict[int, str] = {}
    if backend == "apple-vision":
        b = ensure_vision_bin()
        if not b:
            return raw, "Apple Vision 不可用（需要 macOS + Xcode Command Line Tools）"
        for start in range(0, len(frames), 16):          # 分批，避免单次超时
            chunk = frames[start:start + 16]
            c, out, err = run_cmd([str(b)] + [str(f) for f in chunk], timeout=180)
            if c != 0:
                return raw, f"Apple Vision 失败: {err.strip()[-120:]}"
            for i, t in enumerate(json.loads(out)):
                raw[start + i] = t
        return raw, "ok"
    if backend == "rapidocr":
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            return raw, "未安装 rapidocr-onnxruntime（pip install 'video-digest[ocr]'）"
        eng = RapidOCR()
        for i, f in enumerate(frames):
            res, _ = eng(str(f))
            raw[i] = "\n".join(r[1] for r in (res or []) if len(r) > 2 and float(r[2]) >= 0.5)
        return raw, "ok"
    if backend == "tesseract":
        lang = cfg["ocr"].get("tesseract_lang") or "eng"
        for i, f in enumerate(frames):
            c, out, err = run_cmd(["tesseract", str(f), "-", "-l", lang], timeout=60)
            if c != 0:
                return raw, f"tesseract 失败: {err.strip()[-120:]}"
            raw[i] = out
        return raw, "ok"
    return raw, "未启用 OCR"


def visual_evidence(video: Path, out_dir: Path, duration: float, cfg):
    """返回 (evidence[(t, text)], status, contact_sheet_path|None)。"""
    log("[3/4] 抽帧 + 画面文字识别...")
    frames_dir = out_dir / "evidence_frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir()
    dur = duration if duration and duration > 0 else 600
    n_cfg = int(cfg["ocr"].get("frames") or 48)
    n = n_cfg if dur > 120 else max(8, int(dur / 3))
    interval = dur / n
    c, _, err = run_cmd(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video),
                         "-vf", f"fps=1/{interval:.3f}:start_time=0,scale=960:-2", "-frames:v", str(n),
                         str(frames_dir / "f_%03d.png")], timeout=int(max(120, dur * 0.15)))
    frames = sorted(frames_dir.glob("f_*.png"))
    times = [(i + 0.5) * interval for i in range(len(frames))]   # fps 滤镜第 i 帧 ≈ (i+0.5)*interval
    log(f" -> 候选帧 {len(frames)} 张（每 {interval:.1f}s 一张）")

    backend = resolve_backend(cfg)
    if not frames:
        raw, status = {}, f"抽帧失败: {err.strip()[-120:]}"
    else:
        raw, status = _ocr_frames(frames, backend, cfg)

    # 清洗 + 相邻去重（同一画面文字持续多帧只留第一次）
    evidence, last = [], ""
    for i in range(len(frames)):
        lines = [l.strip(" ※*＊・:：") for l in (raw.get(i) or "").splitlines()]
        lines = [l for l in lines if len(l) >= 3 and not any(k in l.lower() for k in NOISE)]
        text = " | ".join(lines)
        if not text:
            continue
        a, b = set(text), set(last)
        if last and len(a & b) / max(1, len(a | b)) > 0.8:
            continue
        evidence.append((times[i], text[:300]))
        last = text
    keep = int(cfg["ocr"].get("keep") or 30)
    if len(evidence) > keep:
        step = len(evidence) / keep
        evidence = [evidence[int(k * step)] for k in range(keep)]
    log(f" -> 画面文字（{backend}）: {len(evidence)} 条去重后证据" + ("" if status == "ok" else f" | {status}"))
    (out_dir / "ocr.txt").write_text("\n".join(f"[{fmt_ts(t)}] {x}" for t, x in evidence), encoding="utf-8")
    return evidence, status, contact_sheet(frames, out_dir)


def contact_sheet(frames: list[Path], out_dir: Path) -> Path | None:
    """4x4 分镜图。用 ffmpeg 的 image2 序列输入，避免 concat 列表在 Windows 上的路径转义问题。"""
    if not frames:
        return None
    pick = [frames[int(k * len(frames) / 16)] for k in range(min(16, len(frames)))]
    tmp = out_dir / "tile"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    for i, f in enumerate(pick):
        shutil.copy(f, tmp / f"t_{i:02d}.png")
    out = out_dir / "contact_sheet.jpg"
    run_cmd(["ffmpeg", "-loglevel", "error", "-y", "-framerate", "1", "-i", str(tmp / "t_%02d.png"),
             "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2,tile=4x4",
             "-frames:v", "1", "-q:v", "3", str(out)], timeout=60)
    shutil.rmtree(tmp, ignore_errors=True)
    return out if out.exists() else None
