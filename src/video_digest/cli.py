#!/usr/bin/env python3
"""
video_digest - 通用轻量音画视频分析与摘要 CLI

设计考量：
1. 纯 Python 标准库编写主控，零强制三方依赖。
2. 音画结合：原生字幕优先，缺失时降级本地 ASR；抽帧配合本地 OCR (Mac 原生 Vision 或 RapidOCR)。
3. 标准化配置：完全脱离私有硬编码，通过标准环境变量或 CLI 参数配置。
4. 磁盘自愈：临时切片在 finally 中清理，单任务磁盘占用 < 2MB。
"""

import os
import sys
import json
import time
import re
import signal
import shutil
import platform
import subprocess
import argparse
import urllib.request
import urllib.parse
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "video-digest"

class VideoUnavailableError(Exception):
    pass

class CapabilityRouter:
    """跨平台能力探测器"""
    @staticmethod
    def get_os():
        return platform.system().lower()

    @staticmethod
    def get_vision_bin():
        local_bin = PACKAGE_DIR / "vision" / "mac-vision"
        if local_bin.exists() and os.access(local_bin, os.X_OK):
            return local_bin
        
        swift_src = PACKAGE_DIR / "vision" / "mac_vision.swift"
        if CapabilityRouter.get_os() == "darwin" and swift_src.exists() and shutil.which("swiftc"):
            try:
                print(" -> 首次运行：正在为当前系统编译原生 Vision ANE 识别模块...")
                compile_cmd = ["swiftc", "-O", str(swift_src), "-o", str(local_bin)]
                res = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=60)
                if res.returncode == 0 and local_bin.exists():
                    os.chmod(local_bin, 0o755)
                    return local_bin
            except Exception:
                pass
        return None

    @staticmethod
    def get_whisper_backend():
        if CapabilityRouter.get_os() == "darwin":
            try:
                import mlx_whisper
                return "mlx_module"
            except ImportError:
                mlx_py = os.environ.get("MLX_PYTHON")
                if mlx_py and Path(mlx_py).exists():
                    return "mlx_custom"

        custom_cmd = os.environ.get("WHISPER_CLI_PATH")
        if custom_cmd and Path(custom_cmd).exists():
            return "whisper_cpp"
        if shutil.which("whisper-cli"):
            return "whisper_cli_bin"

        if shutil.which("whisper"):
            return "whisper_system"
        return "none"

    @staticmethod
    def get_ocr_backend():
        if CapabilityRouter.get_os() == "darwin" and CapabilityRouter.get_vision_bin():
            return "apple_vision_ane"
        try:
            import rapidocr_onnxruntime
            return "rapidocr"
        except ImportError:
            return "none"

def run_cmd(cmd_args, cwd=None, timeout=60, env_override=None):
    if isinstance(cmd_args, str):
        import shlex
        cmd_args = shlex.split(cmd_args)

    env = os.environ.copy()
    if env_override:
        env.update(env_override)

    p = subprocess.Popen(
        cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, start_new_session=True, cwd=cwd
    )
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, AttributeError):
            pass
        try:
            p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        return -1, "", f"Command timed out after {timeout}s: {' '.join(cmd_args)}"

def step1_fetch_metadata_and_subtitles(url, out_dir, cookies_path=None):
    print("[1/4] 正在获取视频信息与字幕流...")
    meta_json = out_dir / "meta.json"
    srt_out = out_dir / "transcript.srt"

    if os.path.exists(url) and os.path.isfile(url):
        print(f" -> 识别为本地文件: {url}")
        title = Path(url).stem
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(url)]
        c, out, _ = run_cmd(probe_cmd, timeout=10)
        duration = 0
        if c == 0:
            try:
                duration = float(json.loads(out).get("format", {}).get("duration", 0))
            except Exception:
                pass
        if duration <= 0:
            raise VideoUnavailableError("无法读取本地媒体文件时长或文件损坏")
        with open(meta_json, "w", encoding="utf-8") as f:
            json.dump({"title": title, "duration": duration, "uploader": "Local"}, f, ensure_ascii=False)
        print(f" -> 标题: {title} | 时长: {int(duration)//60}分{int(duration)%60}秒")
        return title, duration, "Local", False

    if not shutil.which("yt-dlp"):
        raise RuntimeError("未检测到 yt-dlp，请先安装: pip install yt-dlp 或 brew install yt-dlp")

    cmd = [
        "yt-dlp", "--no-playlist", "--dump-json", "--skip-download",
        "--write-subs", "--write-auto-subs", "--sub-lang", "zh-CN,zh-Hans,zh,ai-zh,en,en-orig,en-US",
        "-o", str(out_dir / "sub"), url
    ]
    if cookies_path and Path(cookies_path).exists():
        cmd.extend(["--cookies", str(cookies_path)])
    elif Path("cookies.txt").exists():
        cmd.extend(["--cookies", "cookies.txt"])

    code, stdout, stderr = run_cmd(cmd, timeout=45)
    title = "Unknown Video"
    duration = 0
    uploader = "Unknown"

    if stdout.strip():
        for line in stdout.splitlines():
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    title = data.get("title", title)
                    duration = data.get("duration", 0)
                    uploader = data.get("uploader", uploader)
                    with open(meta_json, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    break
                except Exception:
                    pass

    if duration <= 0 and title == "Unknown Video":
        raise VideoUnavailableError(f"无法获取有效视频流，请检查链接或网络: {stderr[:150]}")

    has_sub = False
    sub_priority = ["zh-CN", "zh-Hans", "zh", "ai-zh", "en", "en-orig", "en-US"]
    candidate_files = set(list(out_dir.glob("sub*")) + list(out_dir.glob("*.vtt")) + list(out_dir.glob("*.srt")))

    selected_sub = None
    for lang in sub_priority:
        for f in candidate_files:
            if lang in f.name and f.stat().st_size > 500:
                selected_sub = f
                break
        if selected_sub:
            break

    if selected_sub:
        if selected_sub.suffix == ".vtt":
            run_cmd(["ffmpeg", "-y", "-i", str(selected_sub), str(srt_out)], timeout=10)
        else:
            shutil.move(str(selected_sub), str(srt_out))
        if srt_out.exists() and srt_out.stat().st_size >= 500:
            has_sub = True

    sub_size = srt_out.stat().st_size if srt_out.exists() else 0
    print(f" -> 标题: {title} | UP主: {uploader} | 时长: {int(duration)//60}分{int(duration)%60}秒")
    print(f" -> 原生字幕: {'[OK] 命中官方字幕 (' + str(sub_size) + ' 字节)' if has_sub else '[未命中] 准备进入本地转写流程'}")
    return title, duration, uploader, has_sub

def _execute_asr(audio_path, out_dir):
    srt_out = out_dir / "transcript.srt"
    backend = CapabilityRouter.get_whisper_backend()
    print(f" -> 语音识别后端: [{backend}]")

    script_path = PACKAGE_DIR / "mlx_transcribe.py"

    if backend == "mlx_module":
        t_cmd = [sys.executable, str(script_path), str(audio_path), str(out_dir)]
        p_code, _, p_err = run_cmd(t_cmd, timeout=600)
        if p_code == 0 and srt_out.exists() and srt_out.stat().st_size >= 200:
            print(" -> [OK] MLX-Whisper (Metal) 转写成功！")
            return srt_out
        srt_out.unlink(missing_ok=True)
    elif backend == "mlx_custom":
        mlx_py = os.environ.get("MLX_PYTHON")
        t_cmd = [mlx_py, str(script_path), str(audio_path), str(out_dir)]
        p_code, _, p_err = run_cmd(t_cmd, timeout=600)
        if p_code == 0 and srt_out.exists() and srt_out.stat().st_size >= 200:
            print(" -> [OK] 自定义 MLX-Whisper 转写成功！")
            return srt_out
        srt_out.unlink(missing_ok=True)
    elif backend in ["whisper_cpp", "whisper_cli_bin"]:
        w_bin = os.environ.get("WHISPER_CLI_PATH", "whisper-cli")
        m_path = os.environ.get("WHISPER_MODEL_PATH", "models/ggml-large-v3-turbo.bin")
        t_cmd = [w_bin, "-m", m_path, "-f", str(audio_path), "-osrt", "-of", str(out_dir / "transcript"), "-l", "zh"]
        p_code, _, _ = run_cmd(t_cmd, timeout=600)
        if p_code == 0 and srt_out.exists() and srt_out.stat().st_size >= 200:
            print(" -> [OK] whisper-cli 转写成功！")
            return srt_out
        srt_out.unlink(missing_ok=True)
    elif backend == "whisper_system":
        print(" -> 正在调用系统 whisper 命令行...")
        run_cmd(["whisper", str(audio_path), "--model", "turbo", "--language", "zh", "--output_format", "srt", "--output_dir", str(out_dir)], timeout=600)
        for gen_srt in out_dir.glob("*.srt"):
            if gen_srt != srt_out and gen_srt.stat().st_size >= 200:
                shutil.move(str(gen_srt), str(srt_out))
                return srt_out

    print(" -> [提示] 未找到可用的本地 Whisper 工具，跳过语音转文字")
    return srt_out

def step2_pull_audio_and_run_asr(url, out_dir, cookies_path=None):
    srt_out = out_dir / "transcript.srt"
    if srt_out.exists() and srt_out.stat().st_size >= 500:
        return srt_out

    print("[2/4] 获取音频流并进行转写...")
    audio_path = out_dir / "audio.m4a"

    if os.path.exists(url) and os.path.isfile(url):
        c, _, _ = run_cmd(["ffmpeg", "-y", "-i", str(url), "-vn", "-acodec", "aac", str(audio_path)], timeout=30)
        if c != 0 or not audio_path.exists() or audio_path.stat().st_size < 1024:
            print(" -> [提示] 本地文件无有效音频流，降级为纯视觉分析")
            return srt_out
        return _execute_asr(audio_path, out_dir)

    cmd = ["yt-dlp", "--no-playlist", "-f", "ba/b[ext=m4a]/bestaudio", "-o", str(audio_path), url]
    if cookies_path and Path(cookies_path).exists():
        cmd.extend(["--cookies", str(cookies_path)])
    elif Path("cookies.txt").exists():
        cmd.extend(["--cookies", "cookies.txt"])

    code, _, stderr = run_cmd(cmd, timeout=60)
    if code != 0 or not audio_path.exists() or audio_path.stat().st_size < 1024:
        print(f" -> [提示] 音频下载受阻: {stderr[:120]}")
        return srt_out

    return _execute_asr(audio_path, out_dir)

def step3_extract_visual_evidence(url, out_dir, duration, cookies_path=None):
    print("[3/4] 画面关键帧抽取与文字识别...")
    frames_dir = out_dir / "evidence_frames"
    frames_dir.mkdir(exist_ok=True)

    if os.path.exists(url) and os.path.isfile(url):
        video_path = Path(url)
    else:
        video_path = out_dir / "video_360p.mp4"
        cmd = ["yt-dlp", "--no-playlist", "-f", "100046/30011/bv*[height<=360]+ba/b[height<=360]/best", "--merge-output-format", "mp4", "-o", str(video_path), url]
        if cookies_path and Path(cookies_path).exists():
            cmd.extend(["--cookies", str(cookies_path)])
        elif Path("cookies.txt").exists():
            cmd.extend(["--cookies", "cookies.txt"])
        code, _, _ = run_cmd(cmd, timeout=60)
        if not video_path.exists():
            # 兼容 yt-dlp 自动追加扩展名（如 .webm, .mkv）
            candidates = list(out_dir.glob("video_360p.*"))
            if candidates:
                video_path = candidates[0]
        if code != 0 or not video_path.exists() or video_path.stat().st_size < 100 * 1024:
            print(" -> [提示] 未获取到视频画面流，降级为纯文本总结")
            return [], {}, None

    dur_int = max(1, int(duration)) if duration > 0 else 600
    fps_sample = max(1, dur_int // 16)
    thumb_pattern = str(frames_dir / "frame_%03d.png")

    extract_cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", f"fps=1/{fps_sample},mpdecimate,scale=640:-1",
        "-frames:v", "16", thumb_pattern
    ]
    run_cmd(extract_cmd, timeout=30)

    frame_files = sorted(list(frames_dir.glob("frame_*.png")))
    print(f" -> 抽取完成: 获取到 {len(frame_files)} 张关键帧 (对齐 4x4 网格)")

    ocr_results = {}
    ocr_backend = CapabilityRouter.get_ocr_backend()
    print(f" -> 画面文字识别后端: [{ocr_backend}]")

    vision_bin = CapabilityRouter.get_vision_bin()
    if ocr_backend == "apple_vision_ane" and frame_files and vision_bin:
        tasks = [{"index": i, "path": str(f)} for i, f in enumerate(frame_files, 1)]
        batch_json = out_dir / "batch_ocr_task.json"
        with open(batch_json, "w", encoding="utf-8") as f:
            json.dump({"frames": tasks, "enableOcr": True, "enableSaliency": True, "ocrMode": "accurate"}, f)
        o_code, o_out, _ = run_cmd([str(vision_bin), str(batch_json)], timeout=30)
        if o_code == 0:
            try:
                for item in json.loads(o_out):
                    ocr = item.get("ocrText")
                    if ocr:
                        clean_lines = [l.strip() for l in ocr.splitlines() if len(l.strip()) > 2 and not any(k in l for k in ["bilibili", "关注", "点赞", "投币"])]
                        if clean_lines:
                            ocr_results[item["index"]] = "\n".join(clean_lines)
            except Exception:
                pass
        batch_json.unlink(missing_ok=True)
    elif ocr_backend == "rapidocr" and frame_files:
        try:
            from rapidocr_onnxruntime import RapidOCR
            engine = RapidOCR()
            for i, f in enumerate(frame_files, 1):
                res, _ = engine(str(f))
                if res:
                    lines = [line[1] for line in res if line[1] and len(line[1].strip()) > 2]
                    clean_lines = [l.strip() for l in lines if not any(k in l for k in ["bilibili", "关注", "点赞"])]
                    if clean_lines:
                        ocr_results[i] = "\n".join(clean_lines)
        except Exception:
            pass

    contact_sheet = out_dir / "contact_sheet.jpg"
    if frame_files:
        tile_cmd = [
            "ffmpeg", "-y", "-i", thumb_pattern,
            "-filter_complex", "scale=256:144,tile=4x4", "-q:v", "3", str(contact_sheet)
        ]
        run_cmd(tile_cmd, timeout=20)
        if not contact_sheet.exists() and frame_files:
            contact_sheet = frame_files[0]

    return frame_files, ocr_results, contact_sheet

def parse_srt(srt_path):
    if not srt_path or not Path(srt_path).exists():
        return []
    with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read().replace("\r\n", "\n").replace("\r", "\n")

    blocks = content.strip().split("\n\n")
    entries = []
    for b in blocks:
        lines = b.strip().split("\n")
        if len(lines) >= 3 and "-->" in lines[1]:
            time_str = lines[1].split("-->")[0].strip()
            text = " ".join(lines[2:]).strip()
            if text:
                entries.append((time_str, text))
    return entries

def call_llm(prompt, api_key=None, base_url=None, model=None):
    key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("未检测到 API Key，请设置环境变量 OPENAI_API_KEY (或 DEEPSEEK_API_KEY) 或通过 --api-key 传入")

    url = base_url or os.environ.get("OPENAI_BASE_URL")
    if not url:
        if os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
            url = "https://api.deepseek.com/chat/completions"
        else:
            url = "https://api.openai.com/v1/chat/completions"
    elif not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/chat/completions"

    mod = model or os.environ.get("LLM_MODEL") or ("deepseek-chat" if "deepseek" in url else "gpt-4o-mini")

    req_data = {
        "model": mod,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(req_data).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        }
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        res_json = json.loads(resp.read().decode("utf-8"))
        return res_json["choices"][0]["message"]["content"]

def step4_llm_synthesize(title, uploader, duration, srt_entries, ocr_results, out_dir, api_key=None, base_url=None, model=None):
    print("[4/4] 正在整理并生成内容摘要...")

    evidence_text = ""
    for idx, ocr in ocr_results.items():
        clean = " | ".join([line.strip() for line in ocr.splitlines() if line.strip()])
        evidence_text += f"- 关键帧 {idx}: {clean}\n"
    if not evidence_text:
        evidence_text = "(画面未检测到明显字幕、代码或图表)"

    all_lines = [f"[{t}] {txt}" for t, txt in srt_entries]
    total_text = "\n".join(all_lines)

    if len(total_text) > 25000:
        print(f" -> 文本量较长 ({len(total_text)} 字)，自动分段压缩梳理...")
        step_len = 4000
        text_slices = [total_text[i:i+step_len] for i in range(0, len(total_text), step_len)]
        map_summaries = []
        for ci, ts in enumerate(text_slices, 1):
            map_prompt = f"请简明扼要提炼以下视频片段（第{ci}部分）的事件脉络、关键台词与核心事实：\n{ts}"
            try:
                ms = call_llm(map_prompt, api_key=api_key, base_url=base_url, model=model)
                map_summaries.append(f"### 分段 {ci}\n{ms}")
            except Exception:
                map_summaries.append(f"### 分段 {ci}\n{ts[:500]}...")
        distilled_transcript = "\n\n".join(map_summaries)
    else:
        distilled_transcript = total_text if total_text else "(本视频无语音台词或转写结果为空)"

    prompt = f"""你是一位客观严谨的内容整理与分析助手。
请根据以下视频《{title}》（UP主/作者：{uploader}，时长：{int(duration)//60}分{int(duration)%60}秒）的时间轴字幕与画面关键帧文字记录，整理出一份清晰、准确、结构完整的摘要笔记。

【基本要求】：
1. 恪守客观事实：仅依据提供的字幕与画面信息总结，不捏造不存在的细节，遇到不确定的内容标注【推测】。
2. 语言朴实、通俗易懂、层次清晰，避免空泛套话。

【请严格按照以下标记格式输出】：

<<<SUMMARY_CARD>>>
💡 **核心概述**：用一两句话讲清这个视频主要讲了什么 (100字以内)
⏱️ **关键看点与核心节点 (带时间戳)**：精选 3~4 个最具代表性或包含核心信息的节点
🧠 **总结与思考**：视频给出的结论或带来的启发 (150字以内)
<<<END_SUMMARY_CARD>>>

<<<FULL_REPORT>>>
# 内容整理笔记：《{title}》

## 💡 视频核心概述
(概述视频的核心内容、背景与主题)

## ⏱️ 关键脉络与时间线
(按时间顺序梳理视频的逻辑推进过程、重要论述与具体观点，覆盖至片尾)

## 🔍 画面与图表补充信息
(结合关键帧中识别到的代码、参数、图表文字，说明其对语音内容的补充)

## 🧠 总结与要点归纳
(完整总结核心观点，给出清晰明了的结论)
<<<END_FULL_REPORT>>>

【时间轴文字记录】：
{distilled_transcript}

【画面文字记录】：
{evidence_text}
"""

    report_path = out_dir / "report.md"
    summary_path = out_dir / "summary.txt"

    try:
        raw_output = call_llm(prompt, api_key=api_key, base_url=base_url, model=model)

        card_content = ""
        full_report = raw_output

        m_card = re.search(r'<<<SUMMARY_CARD>>>([\s\S]*?)<<<END_SUMMARY_CARD>>>', raw_output)
        if m_card:
            card_content = m_card.group(1).strip()

        m_rep = re.search(r'<<<FULL_REPORT>>>([\s\S]*?)<<<END_FULL_REPORT>>>', raw_output)
        if m_rep:
            full_report = m_rep.group(1).strip()
        else:
            full_report = re.sub(r'<<<SUMMARY_CARD>>>[\s\S]*?<<<END_SUMMARY_CARD>>>', '', raw_output).strip()

        if not card_content:
            card_content = f"💡 《{title}》总结已完成\n时长: {int(duration)//60}分{int(duration)%60}秒 | 作者: {uploader}\n完整笔记详见 report.md"

        with open(report_path, "w", encoding="utf-8") as rf:
            rf.write(full_report)
        with open(summary_path, "w", encoding="utf-8") as sf:
            sf.write(card_content)

        print(" -> [OK] 笔记内容已成功保存 (report.md / summary.txt)")
        return {
            "summary_card": card_content,
            "full_report": full_report
        }
    except Exception as e:
        fallback = f"# {title}\n\n生成摘要时遇到异常: {e}\n\n## 原始参考脉络\n\n{distilled_transcript[:1200]}"
        with open(report_path, "w", encoding="utf-8") as rf:
            rf.write(fallback)
        with open(summary_path, "w", encoding="utf-8") as sf:
            sf.write(fallback[:300])
        return {
            "summary_card": fallback[:300],
            "full_report": fallback
        }

def process_video(url, output_dir=None, cookies_path=None, api_key=None, base_url=None, model=None):
    bvid = "video"
    m = re.search(r'(BV[a-zA-Z0-9]+)', url)
    if m:
        bvid = m.group(1)

    out_dir = Path(output_dir) if output_dir else Path.cwd() / "digest_output" / f"{bvid}_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== 开始处理视频: {url} ===")
    print(f"输出目录: {out_dir}")

    try:
        title, duration, uploader, has_sub = step1_fetch_metadata_and_subtitles(url, out_dir, cookies_path=cookies_path)
        srt_path = step2_pull_audio_and_run_asr(url, out_dir, cookies_path=cookies_path)
        srt_entries = parse_srt(srt_path)
        frame_files, ocr_results, contact_sheet = step3_extract_visual_evidence(url, out_dir, duration, cookies_path=cookies_path)
        artifacts = step4_llm_synthesize(title, uploader, duration, srt_entries, ocr_results, out_dir, api_key=api_key, base_url=base_url, model=model)

        return {
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "summary_card": artifacts["summary_card"],
            "full_report": artifacts["full_report"],
            "contact_sheet": str(contact_sheet) if contact_sheet else None,
            "report_path": str(out_dir / "report.md"),
            "summary_path": str(out_dir / "summary.txt"),
            "srt_path": str(srt_path) if srt_path and Path(srt_path).exists() else None,
            "out_dir": str(out_dir)
        }
    finally:
        for heavy in [out_dir / "video_360p.mp4", out_dir / "audio.m4a", out_dir / "batch_ocr_task.json"]:
            if heavy.exists():
                heavy.unlink(missing_ok=True)
        for gen_vid in out_dir.glob("video_360p.*"):
            gen_vid.unlink(missing_ok=True)
        shutil.rmtree(out_dir / "evidence_frames", ignore_errors=True)

def main():
    parser = argparse.ArgumentParser(description="video-digest: 轻量音画双模态视频总结小工具")
    parser.add_argument("url", help="视频链接、B站 BV 号或本地音视频文件路径")
    parser.add_argument("-o", "--output", help="自定义输出目录（默认保存在当前目录下的 digest_output/）")
    parser.add_argument("--cookies", help="B站或其它网站的 cookies.txt 文件路径")
    parser.add_argument("--api-key", help="OpenAI 或兼容端点的 API Key（亦可设置 OPENAI_API_KEY / DEEPSEEK_API_KEY 环境变量）")
    parser.add_argument("--base-url", help="自定义 API Base URL（例如 https://api.deepseek.com）")
    parser.add_argument("--model", help="指定大语言模型名称")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式打印执行结果")

    args = parser.parse_args()

    try:
        res = process_video(
            args.url,
            output_dir=args.output,
            cookies_path=args.cookies,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model
        )
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print("\n" + "="*60)
            print("📝 【精简摘要卡片】")
            print("="*60)
            print(res["summary_card"])
            print("\n" + "="*60)
            print(f"📄 完整整理笔记已保存: {res['report_path']}")
            if res["contact_sheet"]:
                print(f"🖼️ 关键帧画面拼图已保存: {res['contact_sheet']}")
            print("="*60)
    except VideoUnavailableError as ve:
        print(f"[提示] {ve}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[错误] 执行失败: {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
