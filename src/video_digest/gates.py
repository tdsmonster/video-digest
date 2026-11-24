"""质量闸门：宁可说"证据不足"，也不让 LLM 凭标题编内容。"""
from __future__ import annotations

import re

HALLU_PHRASES = ["谢谢观看", "感谢观看", "请不吝点赞", "订阅", "字幕由", "未经许可", "作词", "作曲", "作詞",
                 "Thank you", "thanks for watching", "Subtitles by", "ご視聴ありがとう"]


class InsufficientEvidenceError(Exception):
    pass


def transcript_quality_gate(entries, duration, trusted: bool):
    """返回 (kept_entries, reliable, note)。

    Whisper 类模型在音乐/静音上会"听出"歌词署名、"谢谢观看"、"oh oh oh" 之类的幻觉，
    段级置信度抓不住，这里按密度/重复/固定短语整体判断。平台字幕 trusted=True 直接放行。
    """
    if not entries:
        return [], False, "无语音文本"
    if trusted:
        return entries, True, "平台字幕"
    counts: dict[str, int] = {}
    for _, _, t in entries:
        counts[t] = counts.get(t, 0) + 1
    kept = []
    for s, e, t in entries:
        dur = max(0.1, e - s)
        if dur >= 10 and len(t) / dur < 0.4:            # 长段只有几个字
            continue
        if counts[t] >= 4 and len(t) < 40:              # 同一句反复出现
            continue
        if len(t) < 25 and any(p.lower() in t.lower() for p in HALLU_PHRASES):
            continue
        if re.search(r"(.{1,8}?)(?:[,，\s]*\1){6,}", t):  # "oh, oh, oh..." 式循环
            continue
        kept.append((s, e, t))
    chars = sum(len(t) for _, _, t in kept)
    uniq = len({t for _, _, t in kept})
    need = max(40, 0.3 * (duration or 60))            # 正常口播 ≥3 字/秒，这里只要求 0.3 字/秒
    # 至少 5 句不同的话；faster-whisper 开 VAD 后段落较长，短视频可能只有 2-4 段，此时要求全部互不相同
    reliable = chars >= need and uniq >= min(5, max(2, len(kept)))
    note = f"本地转写：保留 {len(kept)}/{len(entries)} 段，{chars} 字"
    if not reliable:
        note += "；判定为音乐/无人声/幻觉，不作为台词证据"
    return kept, reliable, note


def evidence_gate(t_ok: bool, evidence, meta: dict, t_note: str, ocr_status: str, mode: str) -> None:
    if t_ok or evidence:
        return
    if len(meta.get("description") or "") >= 30 or len(meta.get("danmaku") or []) >= 50:
        return
    hint = "；可改用 --mode download" if mode == "online" else ""
    raise InsufficientEvidenceError(f"证据不足：{t_note}；画面文字 {ocr_status}；无有效简介/弹幕。拒绝凭标题生成{hint}")
