"""主流程：元数据/字幕 → (下载 → 转写) → 抽帧 OCR → 证据闸门 → LLM 摘要。"""
from __future__ import annotations

import concurrent.futures
import json
import re
import shutil
import time
from pathlib import Path

from . import asr, llm, ocr, prompts, sources
from .gates import evidence_gate, transcript_quality_gate
from .util import fmt_ts, log, parse_srt

MAP_REDUCE_THRESHOLD = 60000   # 字符；超过就先分段提炼再汇总
MAP_CHUNK = 12000


def _map_reduce(cfg, text: str) -> str:
    chunks, cur = [], ""
    for line in text.split("\n"):            # 按行切，不劈开句子
        if len(cur) + len(line) > MAP_CHUNK and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    log(f" -> 文本 {len(text)} 字，分 {len(chunks)} 段并行提炼...")

    def work(ic):
        i, c = ic
        try:
            return f"### 第 {i} 段\n{llm.chat(cfg, prompts.map_prompt(i, len(chunks), c), 6000)}"
        except Exception:
            return f"### 第 {i} 段（提炼失败，截取原文）\n{c[:1500]}"

    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        return "\n\n".join(ex.map(work, enumerate(chunks, 1)))


def synthesize(cfg, meta, transcript, t_ok, t_note, evidence, out_dir: Path) -> dict:
    log(f"[4/4] LLM 合成（{cfg['llm']['model']}）...")
    dur = meta.get("duration") or 0
    t_text = "\n".join(f"[{fmt_ts(s)}] {t}" for s, _, t in transcript) if t_ok else ""
    if len(t_text) > MAP_REDUCE_THRESHOLD:
        t_text = _map_reduce(cfg, t_text)
    ocr_text = "\n".join(f"[{fmt_ts(t)}] {x}" for t, x in evidence)
    chapters = "\n".join(f"[{fmt_ts(c['start'])}] {c['title']}" for c in meta.get("chapters", []))
    srcs = []
    if t_ok:
        srcs.append(t_note)
    if evidence:
        srcs.append(f"画面文字 {len(evidence)} 条")
    if meta.get("description"):
        srcs.append("作者简介")
    dm_text = sources.danmaku_highlights(meta.get("danmaku") or [], dur)
    if dm_text:
        srcs.append(f"弹幕 {len(meta['danmaku'])} 条")

    prompt = prompts.digest_prompt(meta=meta, dur_str=fmt_ts(dur), sources=srcs, weak=not t_ok, t_note=t_note,
                                   chapters=chapters, t_text=t_text, ocr_text=ocr_text, dm_text=dm_text,
                                   language=cfg["output"]["language"])
    report_path, summary_path = out_dir / "report.md", out_dir / "summary.txt"
    try:
        raw = llm.chat(cfg, prompt)
    except Exception as e:
        fb = f"# {meta['title']}\n\n生成失败：{e}\n\n## 转写节选\n\n{t_text[:2000]}"
        report_path.write_text(fb, encoding="utf-8")
        summary_path.write_text(f"《{meta['title']}》摘要生成失败：{e}", encoding="utf-8")
        return {"summary_card": summary_path.read_text(encoding="utf-8"), "full_report": fb, "llm_ok": False, "sources": srcs}
    m_card = re.search(r"<<<CARD>>>([\s\S]*?)<<<END_CARD>>>", raw)
    m_rep = re.search(r"<<<REPORT>>>([\s\S]*?)(?:<<<END_REPORT>>>|$)", raw)
    card = re.sub(r"\*\*|__|^#+\s*", "", m_card.group(1).strip() if m_card else "", flags=re.M)
    report = m_rep.group(1).strip() if m_rep else re.sub(r"<<<CARD>>>[\s\S]*?<<<END_CARD>>>", "", raw).strip()
    if not card:
        card = f"《{meta['title']}》已分析，卡片解析失败，见 report.md"
    card = f"《{meta['title']}》 {meta['uploader']} · {fmt_ts(dur)}\n来源：{'；'.join(srcs) or '仅标题'}\n\n{card}"
    report_path.write_text(report, encoding="utf-8")
    summary_path.write_text(card, encoding="utf-8")
    return {"summary_card": card, "full_report": report, "llm_ok": True, "sources": srcs}


def prune(root: Path, max_items: int = 20, max_gb: float = 2.0) -> None:
    """输出目录 LRU 清理（只清理自动命名的任务目录）。"""
    if not root.exists():
        return
    dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime)

    def size(p):
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    total = size(root)
    while dirs and (len(dirs) > max_items or total > max_gb * 1024 ** 3):
        old = dirs.pop(0)
        total -= size(old)
        shutil.rmtree(old, ignore_errors=True)


def process_video(src: str, cfg: dict, output_dir: str | None = None, mode: str = "auto", keep_frames: bool = False) -> dict:
    """mode: auto = 有平台字幕就在线解析（不下载），否则下载；online = 只用字幕/简介/弹幕；download = 一定下载转写+OCR。

    抛出 sources.VideoUnavailableError / gates.InsufficientEvidenceError。
    """
    url, is_local = sources.normalize_input(src)
    cookies = cfg["bilibili"]["cookies"]
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    tag = m.group(1) if m else re.sub(r"[^0-9A-Za-z_-]", "", Path(url).stem if is_local else url.split("v=")[-1])[:20] or "video"
    root = Path(cfg["output"]["dir"])
    out_dir = Path(output_dir) if output_dir else root / f"{tag}_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"=== 视频解析: {url}\n任务目录: {out_dir}")
    t0 = time.time()
    try:
        meta, sub_source = sources.fetch_metadata_and_subs(url, is_local, out_dir, cookies)
        dur = meta.get("duration") or 0
        used = "download" if is_local else (("online" if sub_source else "download") if mode == "auto" else mode)
        log(f" -> 模式: {'在线（不下载）' if used == 'online' else '下载深度解析'}")
        if used == "online":
            video = audio = None
        elif is_local:
            video = audio = Path(url)
        else:
            video, audio = sources.download_media(url, out_dir, dur, cookies, int(cfg["video"]["max_height"]))

        if sub_source:
            transcript, t_ok, _ = transcript_quality_gate(parse_srt(out_dir / "transcript.srt"), dur, trusted=True)
            t_note = sub_source
        else:
            srt = asr.transcribe(audio, out_dir, dur, cfg) if audio else None
            transcript, t_ok, t_note = transcript_quality_gate(parse_srt(srt), dur, trusted=False)
            if used == "online":
                t_note = "在线模式且平台无字幕"
            elif not audio:
                t_note = "音视频下载失败"
        log(f" -> 台词: {t_note}")
        evidence, ocr_status, contact = ocr.visual_evidence(video, out_dir, dur, cfg) if video else ([], "未抽帧", None)

        evidence_gate(t_ok, evidence, meta, t_note, ocr_status, used)
        art = synthesize(cfg, meta, transcript, t_ok, t_note, evidence, out_dir)
        danmaku_n = len(meta.get("danmaku") or [])
        meta.pop("danmaku", None)
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        res = {
            "ok": art["llm_ok"], "mode": used, "title": meta["title"], "uploader": meta["uploader"], "duration": dur,
            "url": meta.get("webpage_url"), "transcript_source": t_note, "transcript_reliable": t_ok,
            "ocr_items": len(evidence), "ocr_status": ocr_status, "danmaku_count": danmaku_n, "sources": art["sources"],
            "summary_card": art["summary_card"], "full_report": art["full_report"],
            "contact_sheet": str(contact) if contact else None,
            "report_path": str(out_dir / "report.md"), "summary_path": str(out_dir / "summary.txt"),
            "srt_path": str(out_dir / "transcript.srt") if (out_dir / "transcript.srt").exists() else None,
            "out_dir": str(out_dir), "elapsed_s": round(time.time() - t0, 1),
        }
        (out_dir / "result.json").write_text(json.dumps({k: v for k, v in res.items() if k != "full_report"},
                                                        ensure_ascii=False, indent=2), encoding="utf-8")
        return res
    finally:
        for pat in ("media.*", "audio.*", "*.part", "*.ytdl", "audio16k.wav", "src.*"):
            for f in out_dir.glob(pat):
                f.unlink(missing_ok=True)
        if not keep_frames:
            shutil.rmtree(out_dir / "evidence_frames", ignore_errors=True)
        if not output_dir:
            prune(root, int(cfg["output"]["keep_last"]), float(cfg["output"]["max_gb"]))
