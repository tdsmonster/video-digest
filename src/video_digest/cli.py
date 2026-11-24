"""命令行入口。

  video-digest <URL|BV号|本地文件>           解析一个视频（等同于 video-digest run ...）
  video-digest run <src> [--mode M] [--json]
  video-digest doctor [--ping]              检查依赖、后端和 API key
  video-digest bili-login [--check|--qr-only|--poll KEY]
  video-digest watch [--init|--dry-run|--only ID|--max N|--mode M]
  video-digest config-path                  显示配置文件位置
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path

from . import __version__, config

COMMANDS = {"run", "doctor", "bili-login", "watch", "config-path"}

EXIT_UNAVAILABLE, EXIT_NO_EVIDENCE, EXIT_LLM = 1, 2, 3


def _utf8_stdio():
    for s in (sys.stdout, sys.stderr):      # Windows 控制台默认 GBK/cp1252，中文会乱码或报错
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def cmd_run(a, cfg) -> int:
    from .gates import InsufficientEvidenceError
    from .pipeline import process_video
    from .sources import VideoUnavailableError

    if a.language:
        cfg["output"]["language"] = a.language
    try:
        r = process_video(a.src, cfg, a.output, a.mode, a.keep_frames)
    except VideoUnavailableError as e:
        print(f"[失败] 视频不可用: {e}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except InsufficientEvidenceError as e:
        print(f"[失败] {e}", file=sys.stderr)
        return EXIT_NO_EVIDENCE
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 60 + "\n" + r["summary_card"] + "\n" + "=" * 60)
        print(f"报告: {r['report_path']}" + (f"\n分镜: {r['contact_sheet']}" if r["contact_sheet"] else ""))
    return 0 if r["ok"] else EXIT_LLM


def cmd_doctor(a, cfg) -> int:
    from . import asr, ocr
    from .util import run_cmd

    ok = True

    def line(status, name, detail=""):
        mark = {"ok": "✅", "warn": "⚠️ ", "bad": "❌"}[status]
        print(f"{mark} {name}" + (f"：{detail}" if detail else ""))

    print(f"video-digest {__version__} · Python {platform.python_version()} · {platform.system()} {platform.machine()}")
    print(f"配置文件：{cfg['_path'] or '（未找到，使用默认值）'}")
    print(f"输出目录：{cfg['output']['dir']}\n")

    for tool, need in (("yt-dlp", True), ("ffmpeg", True), ("ffprobe", True)):
        p = shutil.which(tool)
        if p:
            c, out, _ = run_cmd([tool, "--version"] if tool == "yt-dlp" else [tool, "-version"], timeout=20)
            line("ok", tool, (out.splitlines() or [""])[0][:60])
        else:
            ok = False
            line("bad", tool, "未找到，必须安装")

    b = asr.resolve_backend(cfg)
    hint = {"none": "pip install 'video-digest[whisper]'（faster-whisper，任何平台）" +
            ("；Apple Silicon 可装 'video-digest[mlx]'" if asr.is_apple_silicon() else "")}
    line("ok" if b != "none" else "warn", "语音转写", b if b != "none" else "无后端，只能用平台字幕。" + hint["none"])

    o = ocr.resolve_backend(cfg)
    if o == "apple-vision":
        bin_ = ocr.ensure_vision_bin()
        line("ok" if bin_ else "warn", "画面文字", "apple-vision" + ("" if bin_ else "（编译失败，需要 Xcode Command Line Tools：xcode-select --install）"))
    else:
        line("ok" if o != "none" else "warn", "画面文字", o if o != "none" else "无后端。pip install 'video-digest[ocr]'（RapidOCR）")

    key = config.api_key(cfg)
    llm_cfg = cfg["llm"]
    local = any(h in llm_cfg["base_url"] for h in ("localhost", "127.0.0.1"))
    if key or local:
        line("ok", "LLM", f"{llm_cfg['model']} @ {llm_cfg['base_url']}" + ("" if key else "（本地，无 key）"))
    else:
        ok = False
        line("bad", "LLM", "没有 API key：设置环境变量 VIDEO_DIGEST_API_KEY（或 DEEPSEEK_API_KEY / OPENAI_API_KEY）")
    if a.ping and (key or local):
        from . import llm

        try:
            r = llm.chat(cfg, "只回复两个字：在线", 2000)
            line("ok", "LLM 连通", r.strip()[:40])
        except Exception as e:
            ok = False
            line("bad", "LLM 连通", str(e)[:200])

    ck = cfg["bilibili"]["cookies"]
    if Path(ck).exists():
        line("ok", "B站登录", f"cookie {ck}（--ping 时会在线校验）")
        if a.ping:
            from .bili_login import check

            try:
                r = check(ck)
                line("ok" if r.get("isLogin") else "warn", "B站登录状态", r.get("uname") or "cookie 已失效，重新 bili-login")
            except Exception as e:
                line("warn", "B站登录状态", str(e)[:120])
    else:
        line("warn", "B站登录", "未登录（可选）：拿 B站 AI 字幕需要 `video-digest bili-login`")

    srcs = cfg["watch"].get("sources") or []
    line("ok" if srcs else "warn", "订阅追更", f"{len(srcs)} 个来源" if srcs else "未配置（可选）")
    print("\n" + ("一切就绪。" if ok else "有必需项缺失，见上面 ❌。"))
    return 0 if ok else 1


def cmd_bili_login(a, cfg) -> int:
    from . import bili_login

    ck = cfg["bilibili"]["cookies"]
    if a.check:
        r = bili_login.check(ck)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("isLogin") else 1
    if a.qr_only:
        print(json.dumps(bili_login.generate_qr(), ensure_ascii=False))
        return 0
    if a.poll:
        return bili_login.poll(a.poll, ck, a.timeout)
    return bili_login.interactive(ck)


def cmd_watch(a, cfg) -> int:
    from . import watch

    return watch.run(cfg, init=a.init, dry_run=a.dry_run, only=a.only, max_n=a.max, mode=a.mode, config_path=cfg["_path"])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="video-digest", description="视频 → 字幕/转写 + 画面文字 → 有证据的 AI 摘要")
    ap.add_argument("-V", "--version", action="version", version=f"video-digest {__version__}")
    ap.add_argument("--config", help="配置文件路径（默认自动查找，见 video-digest config-path）；放在子命令前后都可以")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="解析一个视频")
    r.add_argument("src", help="视频链接、BV/av 号、App 分享文案或本地文件路径")
    r.add_argument("-o", "--output", help="输出目录（默认自动命名并定期清理）")
    r.add_argument("--mode", choices=["auto", "online", "download"], default="auto",
                   help="auto：有平台字幕就不下载；online：只用字幕/简介/弹幕；download：强制下载转写+画面文字")
    r.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    r.add_argument("--language", help="摘要语言（zh / en / ja / ko ...），覆盖配置")
    r.add_argument("--keep-frames", action="store_true", help="保留候选帧（调试用）")

    d = sub.add_parser("doctor", help="检查环境")
    d.add_argument("--ping", action="store_true", help="实际调用一次 LLM、校验 B站 cookie")

    b = sub.add_parser("bili-login", help="B站扫码登录")
    b.add_argument("--check", action="store_true")
    b.add_argument("--qr-only", action="store_true")
    b.add_argument("--poll", metavar="KEY")
    b.add_argument("--timeout", type=int, default=170)

    w = sub.add_parser("watch", help="订阅追更（配合定时任务）")
    w.add_argument("--init", action="store_true", help="只把现有视频登记为基线，不解析")
    w.add_argument("--dry-run", action="store_true", help="只列出待处理视频")
    w.add_argument("--only", metavar="ID", help="只（重新）处理某个视频")
    w.add_argument("--max", type=int, help="本次最多处理几个")
    w.add_argument("--mode", choices=["auto", "online", "download"])

    sub.add_parser("config-path", help="显示配置文件和数据目录位置")
    return ap


def main(argv=None) -> int:
    _utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    # --config 放在子命令前后都行：先摘出来
    cfg_path = None
    for i, x in enumerate(argv):
        if x == "--config" and i + 1 < len(argv):
            cfg_path = argv[i + 1]
            del argv[i:i + 2]
            break
        if x.startswith("--config="):
            cfg_path = x.split("=", 1)[1]
            del argv[i]
            break
    # 允许 `video-digest <URL>` 省略 run
    first = next((i for i, x in enumerate(argv) if not x.startswith("-")), None)
    if first is not None and argv[first] not in COMMANDS:
        argv.insert(first, "run")
    a = build_parser().parse_args(argv)
    if not a.cmd:
        build_parser().print_help()
        return 0
    cfg = config.load(cfg_path)
    if a.cmd == "config-path":
        print(f"使用中的配置：{cfg['_path'] or '（无）'}\n默认位置：{config.config_dir() / 'config.toml'}\n数据目录：{config.data_dir()}")
        return 0
    return {"run": cmd_run, "doctor": cmd_doctor, "bili-login": cmd_bili_login, "watch": cmd_watch}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
