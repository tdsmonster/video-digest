#!/usr/bin/env python3
"""独立的 ASR 子进程脚本（不依赖本包，可以用另一个 Python 解释器跑，例如单独的 MLX venv）。

用法: python asr_worker.py <mlx|faster-whisper> <audio.wav> <out_dir> [--model M] [--language L] [--device D]
输出: <out_dir>/transcript.srt + transcript.json

- 语言默认自动检测
- condition_on_previous_text=False：避免一句幻觉被后面的窗口复读
- 段级过滤：compression_ratio>2.4（复读）、no_speech_prob>0.6 且 avg_logprob<-1（无人声）
  音乐/幻觉的整体判定在 gates.transcript_quality_gate 里做
"""
import argparse
import json
import sys
from pathlib import Path

ZH_PROMPT = "以下是普通话的句子，使用简体中文和标点。"


def srt_time(sec):
    h, m, s = int(sec // 3600), int(sec % 3600 // 60), int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d},{int((sec - int(sec)) * 1000):03d}"


def keep(seg):
    if seg["compression_ratio"] > 2.4:
        return False
    if seg["no_speech_prob"] > 0.6 and seg["avg_logprob"] < -1.0:
        return False
    return bool(seg["text"].strip())


def run_mlx(audio, model, language):
    import mlx_whisper

    res = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model or "mlx-community/whisper-large-v3-turbo",
        language=language or None,
        condition_on_previous_text=False,
        initial_prompt=ZH_PROMPT if language in ("", None, "zh") else None,
        verbose=None,
    )
    segs = [{"start": s["start"], "end": s["end"], "text": s.get("text", ""),
             "compression_ratio": s.get("compression_ratio", 0), "no_speech_prob": s.get("no_speech_prob", 0),
             "avg_logprob": s.get("avg_logprob", 0)} for s in res.get("segments", [])]
    return res.get("language"), segs


def run_faster_whisper(audio, model, language, device):
    from faster_whisper import WhisperModel

    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if not model:   # CPU 上 turbo 太慢，默认 small；有 GPU 用 turbo
        model = "large-v3-turbo" if device == "cuda" else "small"
    compute = "float16" if device == "cuda" else "int8"
    print(f"[faster-whisper] model={model} device={device} compute={compute}", file=sys.stderr, flush=True)
    wm = WhisperModel(model, device=device, compute_type=compute)
    it, info = wm.transcribe(
        audio, language=language or None, condition_on_previous_text=False, vad_filter=True,
        initial_prompt=ZH_PROMPT if language in ("", None, "zh") else None,
    )
    segs = [{"start": s.start, "end": s.end, "text": s.text, "compression_ratio": s.compression_ratio,
             "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob} for s in it]
    return info.language, segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", choices=["mlx", "faster-whisper"])
    ap.add_argument("audio")
    ap.add_argument("out_dir")
    ap.add_argument("--model", default="")
    ap.add_argument("--language", default="")
    ap.add_argument("--device", default="auto")
    a = ap.parse_args()
    out = Path(a.out_dir)
    if a.backend == "mlx":
        lang, segs = run_mlx(a.audio, a.model, a.language)
    else:
        lang, segs = run_faster_whisper(a.audio, a.model, a.language, a.device)
    kept = [s for s in segs if keep(s)]
    (out / "transcript.json").write_text(json.dumps({"language": lang, "segments": kept}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    with open(out / "transcript.srt", "w", encoding="utf-8") as f:
        for i, s in enumerate(kept, 1):
            f.write(f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text'].strip()}\n\n")
    print(f"[{a.backend}] lang={lang} segments={len(kept)}/{len(segs)}", file=sys.stderr)


if __name__ == "__main__":
    main()
