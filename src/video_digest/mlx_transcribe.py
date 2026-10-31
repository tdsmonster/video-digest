#!/usr/bin/env python3
"""
mlx_transcribe.py - 基于 MLX-Whisper 的离线转写脚本
"""

import sys
import os
import json
from pathlib import Path

def to_srt_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def clean_hallucinations(segments):
    """过滤 Whisper 在长静音时偶发的复读现象"""
    cleaned = []
    last_text = ""
    repeat_count = 0
    
    for seg in segments:
        txt = seg.get("text", "").strip()
        if not txt:
            continue
        if txt == last_text:
            repeat_count += 1
            if repeat_count > 3:
                continue
        else:
            last_text = txt
            repeat_count = 1
            
        cleaned.append(seg)
    return cleaned

def main():
    if len(sys.argv) < 3:
        print("Usage: mlx_transcribe.py <audio_path> <out_dir>", file=sys.stderr)
        sys.exit(1)
        
    audio_path = sys.argv[1]
    out_dir = Path(sys.argv[2])
    srt_out = out_dir / "transcript.srt"
    json_out = out_dir / "transcript.json"

    try:
        import mlx_whisper
    except ImportError:
        print("mlx_whisper 未安装，请先 pip install mlx-whisper", file=sys.stderr)
        sys.exit(2)

    print(f"[MLX-Whisper] 开始转写音频: {audio_path}")
    model_name = os.environ.get("MLX_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
    
    try:
        res = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=model_name,
            language=os.environ.get("WHISPER_LANG", "zh"),
            temperature=0.0
        )
    except Exception as e:
        print(f"[MLX-Whisper] 转写失败: {e}", file=sys.stderr)
        sys.exit(3)

    segments = clean_hallucinations(res.get("segments", []))
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)

    with open(srt_out, "w", encoding="utf-8") as f:
        for idx, seg in enumerate(segments, 1):
            start_str = to_srt_time(seg.get("start", 0))
            end_str = to_srt_time(seg.get("end", 0))
            txt = seg.get("text", "").strip()
            f.write(f"{idx}\n{start_str} --> {end_str}\n{txt}\n\n")

    print(f"[MLX-Whisper] 转写完成，字幕已写入: {srt_out}")

if __name__ == "__main__":
    main()
