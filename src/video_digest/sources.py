"""输入归一化 + yt-dlp：元数据、平台字幕、弹幕、媒体下载。"""
from __future__ import annotations

import html
import json
import re
import shutil
import urllib.request
from pathlib import Path

from .util import fmt_ts, log, run_cmd, ytdlp_cmd

SUB_PRIORITY = ["zh-CN", "zh-Hans", "zh-Hant", "zh", "ai-zh", "en", "ai-en", "ja", "ko"]


class VideoUnavailableError(Exception):
    pass


def normalize_input(src: str) -> tuple[str, bool]:
    """裸 BV/av 号、b23 短链、App 分享文案、本地路径 → (yt-dlp 可用的输入, 是否本地文件)。"""
    s = src.strip()
    p = Path(s).expanduser()
    if p.is_file():
        return str(p.resolve()), True
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", s):
        return f"https://www.bilibili.com/video/{s}", False
    if re.fullmatch(r"av\d+", s, re.I):
        return f"https://www.bilibili.com/video/{s.lower()}", False
    if not re.match(r"https?://", s) and re.match(r"(www\.|m\.)?(bilibili\.com|b23\.tv|youtube\.com|youtu\.be)", s):
        s = "https://" + s
    m = re.search(r"https?://[^\s　【】\"'<>]+", s)      # 「【标题】 https://b23.tv/xxx」
    if m:
        s = m.group(0)
    if "b23.tv" in s:
        try:
            req = urllib.request.Request(s, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                s = r.geturl()
        except Exception:
            pass
    bv = re.search(r"bilibili\.com/video/(BV[0-9A-Za-z]{10}|av\d+)", s)
    if bv:                                                 # 去掉追踪参数，保留分 P
        pn = re.search(r"[?&]p=(\d+)", s)
        s = f"https://www.bilibili.com/video/{bv.group(1)}" + (f"?p={pn.group(1)}" if pn else "")
    return s, False


def is_bilibili(url: str) -> bool:
    return bool(re.search(r"bilibili\.com|b23\.tv", url))


def ytdlp_base(url: str, cookies: str | None) -> list[str]:
    cmd = ytdlp_cmd() + ["--no-playlist", "--no-warnings", "--retries", "5"]
    if is_bilibili(url):
        # yt-dlp #17847：B站备用 CDN 不带 Range 头会回 HTTP 567
        cmd += ["--add-headers", "Range:bytes=0-"]
        if cookies and Path(cookies).exists():
            cmd += ["--cookies", cookies]
    return cmd


def probe_duration(path: str) -> float:
    c, out, _ = run_cmd(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path], timeout=30)
    if c == 0:
        try:
            return float(json.loads(out).get("format", {}).get("duration", 0))
        except Exception:
            pass
    return 0.0


def fetch_metadata_and_subs(url: str, is_local: bool, out_dir: Path, cookies: str | None):
    """返回 (meta, sub_source)。有平台字幕时写 out_dir/transcript.srt。"""
    log("[1/4] 元数据与平台字幕...")
    meta = {"title": "Unknown", "duration": 0, "uploader": "Unknown", "description": "", "tags": [],
            "chapters": [], "danmaku": [], "webpage_url": url}
    srt_out = out_dir / "transcript.srt"

    if is_local:
        dur = probe_duration(url)
        if dur <= 0:
            raise VideoUnavailableError("本地文件无法解码（ffprobe 读不到时长）")
        meta.update(title=Path(url).stem, duration=dur, uploader="本地文件")
        log(f" -> 本地文件: {meta['title']} | {fmt_ts(dur)}")
        return meta, None

    # -J 只取元数据；字幕另行下载（-j 是 simulate 模式，和 --write-subs 混用时字幕不落盘）
    code, out, err = run_cmd(ytdlp_base(url, cookies) + ["-J", url], timeout=90)
    try:
        data = json.loads(out)
    except Exception:
        raise VideoUnavailableError(f"取不到元数据（已删除/锁区/需登录？）: {err.strip()[-200:]}")
    meta.update(title=data.get("title") or "Unknown", duration=data.get("duration") or 0,
                uploader=data.get("uploader") or data.get("channel") or "Unknown",
                description=(data.get("description") or "")[:3000], tags=(data.get("tags") or [])[:20],
                chapters=[{"start": c.get("start_time", 0), "title": c.get("title", "")} for c in (data.get("chapters") or [])],
                webpage_url=data.get("webpage_url") or url)

    # YouTube 只拉选中的一种字幕（多语种并发拉 timedtext 易 429）；B站 -J 不列字幕，按优先级直接拉
    sub_args = None
    if is_bilibili(url):
        sub_args = ["--write-subs", "--sub-langs", ",".join(SUB_PRIORITY + ["danmaku"])]
    else:
        manual = data.get("subtitles") or {}
        auto = data.get("automatic_captions") or {}
        orig = (data.get("language") or "").split("-")[0]
        pick = next((l for l in SUB_PRIORITY if l in manual), None) or next((l for l in manual if l.split("-")[0] == orig), None)
        if pick:
            sub_args = ["--write-subs", "--sub-langs", pick]
        else:
            pick = next((l for l in auto if orig and l == orig), None) or next((l for l in auto if l.endswith("-orig")), None)
            if pick:
                sub_args = ["--write-auto-subs", "--sub-langs", pick]
    if sub_args:
        run_cmd(ytdlp_base(url, cookies) + ["--skip-download"] + sub_args +
                ["--sub-format", "srt/vtt/best", "-o", str(out_dir / "src.%(ext)s"), url],
                timeout=90 if is_bilibili(url) else 45)     # YouTube 字幕接口常卡（PO token/429），超时就走本地转写

    sub_source = None
    files = [f for f in out_dir.glob("src.*") if f.suffix in (".srt", ".vtt") and f.stat().st_size > 200]
    for lang in SUB_PRIORITY + sorted({f.name.split(".")[-2] for f in files}):
        hit = next((f for f in files if f.name.split(".")[-2] == lang), None)
        if not hit:
            continue
        if hit.suffix == ".vtt":
            run_cmd(["ffmpeg", "-loglevel", "error", "-y", "-i", str(hit), str(srt_out)], timeout=30)
        else:
            shutil.copy(hit, srt_out)
        if srt_out.exists() and srt_out.stat().st_size > 200:
            sub_source = f"平台字幕({lang})"
            break
    dm = out_dir / "src.danmaku.xml"
    meta["danmaku"] = parse_danmaku(dm) if dm.exists() else []
    for f in out_dir.glob("src.*"):
        f.unlink(missing_ok=True)

    log(f" -> 标题: {meta['title']} | 作者: {meta['uploader']} | 时长: {fmt_ts(meta['duration'])}")
    extra = ""
    if is_bilibili(url):
        login = "已登录" if cookies and Path(cookies).exists() else "未登录(拿不到 AI 字幕)"
        extra = f" | B站 {login} | 弹幕 {len(meta['danmaku'])} 条"
    log(f" -> 字幕: {sub_source or '无'}{extra}")
    return meta, sub_source


def parse_danmaku(xml_path: Path) -> list[tuple[float, str]]:
    out = []
    for m in re.finditer(r'<d p="([\d.]+),[^"]*">([^<]*)</d>', xml_path.read_text(encoding="utf-8", errors="ignore")):
        t = html.unescape(m.group(2)).strip()
        if t:
            out.append((float(m.group(1)), t))
    return sorted(out)


def danmaku_highlights(dm, duration, top: int = 6) -> str:
    """按时间分桶找弹幕最密的几段，列出每段最常见的几条。代表观众反应，不是视频内容。"""
    if len(dm) < 20:
        return ""
    dur = duration or max(t for t, _ in dm) or 1
    width = max(10.0, dur / 40)
    bins: dict[int, list[str]] = {}
    for t, x in dm:
        bins.setdefault(int(t // width), []).append(x)
    avg = len(dm) / max(1, len(bins))
    lines = []
    for b, xs in sorted(bins.items(), key=lambda kv: -len(kv[1]))[:top]:
        if len(xs) < max(5, avg * 1.5):
            continue
        cnt: dict[str, int] = {}
        for x in xs:
            cnt[x[:30]] = cnt.get(x[:30], 0) + 1
        common = " / ".join(k for k, _ in sorted(cnt.items(), key=lambda kv: -kv[1])[:4])
        lines.append((b * width, f"[{fmt_ts(b * width)}] {len(xs)} 条：{common}"))
    return "\n".join(l for _, l in sorted(lines))


def download_media(url: str, out_dir: Path, duration: float, cookies: str | None, max_height: int = 1080):
    """返回 (video_path | None, audio_path | None)。下载 ≤max_height 的视频 + 最佳音轨，失败退回只下音频。

    画面文字识别对分辨率敏感：录屏/PPT 里的小字在 480p 下约有两成认不出来（见 README「清晰度」一节），
    所以默认 1080p；语音转写不受影响（音轨总是取最佳）。avc 优先，解码兼容性最好。
    """
    h = int(max_height or 1080)
    log(f"[2/4] 下载 ≤{h}p 视频 + 音轨...")
    tmo = int(max(300, (duration or 0) * (0.6 + h / 1080)))
    fmt = (f"bv*[height<={h}][vcodec^=avc]+ba/bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b")
    cmd = ytdlp_base(url, cookies) + ["-f", fmt, "--merge-output-format", "mp4", "-o", str(out_dir / "media.%(ext)s"), url]
    _, _, err = run_cmd(cmd, timeout=tmo)
    vids = [f for f in out_dir.glob("media.*") if f.suffix in (".mp4", ".mkv", ".webm") and f.stat().st_size > 100_000]
    if vids:
        return vids[0], vids[0]
    log(f" -> [警告] 视频下载失败，改为只下音频: {err.strip()[-160:]}")
    _, _, err = run_cmd(ytdlp_base(url, cookies) + ["-f", "ba/b", "-o", str(out_dir / "audio.%(ext)s"), url], timeout=tmo)
    auds = [f for f in out_dir.glob("audio.*") if f.stat().st_size > 10_000]
    if auds:
        return None, auds[0]
    log(f" -> [失败] 音频也下载失败: {err.strip()[-160:]}")
    return None, None
