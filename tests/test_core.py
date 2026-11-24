"""不联网、不需要 ffmpeg 的单元测试，三平台 CI 都跑。"""
import os
import sys

import pytest

from video_digest import cli, config
from video_digest.gates import InsufficientEvidenceError, evidence_gate, transcript_quality_gate
from video_digest.sources import danmaku_highlights, normalize_input
from video_digest.util import fmt_ts, parse_srt, run_cmd, write_srt
from video_digest.watch import extract_links, screen_links


def test_normalize_bv_and_share_text():
    assert normalize_input("BV16Cxp6aEGn") == ("https://www.bilibili.com/video/BV16Cxp6aEGn", False)
    s = "【标题】 https://www.bilibili.com/video/BV16Cxp6aEGn/?spm_id_from=333&p=2&share=x"
    assert normalize_input(s)[0] == "https://www.bilibili.com/video/BV16Cxp6aEGn?p=2"
    assert normalize_input("youtu.be/abc")[0] == "https://youtu.be/abc"


def test_normalize_local_file(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    path, local = normalize_input(str(f))
    assert local and os.path.samefile(path, f)


def test_srt_roundtrip(tmp_path):
    segs = [(0.0, 2.5, "你好"), (3661.2, 3663.0, "world")]
    p = tmp_path / "a.srt"
    write_srt(segs, p)
    got = parse_srt(p)
    assert [t for *_, t in got] == ["你好", "world"]
    assert abs(got[1][0] - 3661.2) < 0.01
    assert fmt_ts(3661) == "1:01:01" and fmt_ts(75) == "01:15"


def test_quality_gate_rejects_music_hallucination():
    entries = [(i * 10.0, i * 10.0 + 9, "作曲 李宗盛") for i in range(8)] + [(100, 140, "oh")]
    kept, ok, note = transcript_quality_gate(entries, 180, trusted=False)
    assert not ok and "幻觉" in note


def test_quality_gate_accepts_speech():
    entries = [(i * 3.0, i * 3.0 + 3, f"这是第{i}句正常的讲解内容，信息量足够") for i in range(40)]
    _, ok, _ = transcript_quality_gate(entries, 120, trusted=False)
    assert ok


def test_quality_gate_accepts_few_long_segments():
    # faster-whisper + VAD：37 秒讲话只切成 4 段
    entries = [(0, 9, "大家好，今天我们介绍一个视频摘要工具，它先读取平台字幕"), (9, 18, "如果没有字幕就下载视频在本地做语音识别然后抽取画面"),
               (18, 28, "识别屏幕上的文字比如幻灯片和代码最后交给大语言模型"), (28, 37, "如果证据不足它会直接失败而不是凭标题瞎编")]
    _, ok, _ = transcript_quality_gate(entries, 37, trusted=False)
    assert ok


def test_evidence_gate():
    with pytest.raises(InsufficientEvidenceError):
        evidence_gate(False, [], {"description": ""}, "无", "未抽帧", "online")
    evidence_gate(False, [(1.0, "slide text")], {}, "无", "ok", "download")   # 有画面文字就放行


def test_danmaku_highlights():
    dm = [(5.0, "前排")] * 10 + [(100.0, "高能")] * 40 + [(t * 7.0, "普通") for t in range(30)]
    out = danmaku_highlights(dm, 300)
    assert "高能" in out


def test_links():
    text = "代码 https://github.com/a/b 论文 https://arxiv.org/abs/2401.00001。 BV1xx411c7mD"
    links = extract_links(text, "BV16Cxp6aEGn")
    assert links[0] == "https://github.com/a/b" and "arxiv.org/abs/2401.00001" in links[1]
    assert screen_links("见 github.com / fancyboi 999 / repo") == ["https://github.com/fancyboi999/repo"]


def test_config_env_override(monkeypatch, tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nmodel = "x"\napi_key = "file-key"\n', encoding="utf-8")
    monkeypatch.setenv("VIDEO_DIGEST_MODEL", "y")
    monkeypatch.setenv("VIDEO_DIGEST_API_KEY", "env-key")
    cfg = config.load(str(p))
    assert cfg["llm"]["model"] == "y" and config.api_key(cfg) == "env-key"


def test_run_cmd_timeout_and_missing():
    code, _, _ = run_cmd([sys.executable, "-c", "import time; time.sleep(10)"], timeout=1)
    assert code == -1
    assert run_cmd(["definitely-not-a-command-xyz"])[0] == 127


def test_cli_inserts_run(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "cmd_run", lambda a, cfg: seen.setdefault("src", a.src) and 0)
    cli.main(["BV16Cxp6aEGn"])
    assert seen["src"] == "BV16Cxp6aEGn"


def test_cli_config_anywhere(monkeypatch, tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nmodel = "from-file"\n', encoding="utf-8")
    seen = {}
    monkeypatch.setattr(cli, "cmd_run", lambda a, cfg: seen.setdefault("model", cfg["llm"]["model"]) and 0)
    cli.main(["run", "BV16Cxp6aEGn", "--config", str(p)])
    assert seen["model"] == "from-file"
