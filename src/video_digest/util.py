"""跨平台小工具：子进程（可超时整组杀掉）、时间格式、SRT 解析。"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

BILI_NO_PROXY = "bilibili.com,bilivideo.com,hdslb.com,b23.tv,127.0.0.1,localhost"
IS_WIN = os.name == "nt"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def child_env(extra: dict | None = None) -> dict:
    env = os.environ.copy()
    np_ = ",".join(x for x in [env.get("NO_PROXY", ""), BILI_NO_PROXY] if x)
    env["NO_PROXY"] = env["no_proxy"] = np_
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


def run_cmd(args, cwd=None, timeout=60, env=None):
    """运行命令，返回 (returncode, stdout, stderr)。超时会连子进程一起杀掉；超时返回码 -1。"""
    args = [str(a) for a in args]
    kw: dict[str, Any] = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env or child_env(),
              text=True, encoding="utf-8", errors="replace")
    if IS_WIN:
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True
    try:
        p = subprocess.Popen(args, **kw)
    except FileNotFoundError as e:
        return 127, "", f"command not found: {args[0]} ({e})"
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return -1, "", f"timeout after {timeout}s: {' '.join(args)[:200]}"


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        p.kill()
    except OSError:
        pass


def fmt_ts(sec) -> str:
    sec = int(max(0, sec or 0))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def srt_time(sec: float) -> str:
    h, m, s = int(sec // 3600), int(sec % 3600 // 60), int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d},{int((sec - int(sec)) * 1000):03d}"


def srt_time_to_sec(t: str) -> float:
    parts = t.strip().replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        pass
    return 0.0


def parse_srt(path) -> list[tuple[float, float, str]]:
    """SRT / VTT → [(start, end, text)]。"""
    if not path or not Path(path).exists():
        return []
    content = Path(path).read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
    entries = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        a, _, z = lines[idx].partition("-->")
        text = re.sub(r"<[^>]+>", "", " ".join(lines[idx + 1:])).strip()
        if text:
            entries.append((srt_time_to_sec(a), srt_time_to_sec(z.split()[0] if z.split() else a), text))
    return entries


def write_srt(segments, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(segments, 1):
            f.write(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t.strip()}\n\n")
