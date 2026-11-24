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


def load_wav16k(path):
    """直接读 16k 单声道 wav（pipeline 已用 ffmpeg 转好），不走 PyAV：
    新版 PyAV 去掉了 faster-whisper 传的 metadata_errors 参数，会直接报错。"""
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "需要 16k 单声道 16bit wav"
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def run_faster_whisper(audio, model, language, device):
    from faster_whisper import WhisperModel

    want_cuda = device == "cuda"
    if device == "auto":
        try:
            import ctranslate2
            want_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            want_cuda = False
    samples = load_wav16k(audio)

    def attempt(dev):
        m = model or ("large-v3-turbo" if dev == "cuda" else "small")   # CPU 上 turbo 太慢，默认 small
        compute = "float16" if dev == "cuda" else "int8"
        print(f"[faster-whisper] model={m} device={dev} compute={compute}", file=sys.stderr, flush=True)
        wm = WhisperModel(m, device=dev, compute_type=compute)
        it, info = wm.transcribe(
            samples, language=language or None, condition_on_previous_text=False, vad_filter=True,
            initial_prompt=ZH_PROMPT if language in ("", None, "zh") else None,
        )
        segs = [{"start": s.start, "end": s.end, "text": s.text, "compression_ratio": s.compression_ratio,
                 "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob} for s in it]   # 真正推理在这里
        return info.language, segs

    if want_cuda:
        try:
            return attempt("cuda")
        except Exception as e:
            # 有 NVIDIA 显卡但没装 CUDA 12 / cuDNN 9 运行库（Windows 上常见：cublas64_12.dll not found）
            print(f"[faster-whisper] GPU 不可用，改用 CPU：{str(e)[:160]}", file=sys.stderr, flush=True)
    return attempt("cpu")


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
