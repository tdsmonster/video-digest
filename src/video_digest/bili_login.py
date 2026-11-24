"""B站扫码登录 → Netscape 格式 cookie 文件（权限 600）。登录后 yt-dlp 能拿到 B站 AI 字幕，watch 能读私密收藏夹。

  video-digest bili-login            终端里显示二维码，手机 B站 App 扫码确认
  video-digest bili-login --check    检查现有 cookie 是否有效
  video-digest bili-login --qr-only  只生成二维码（JSON 输出 qrcode_key 和 PNG 路径，给机器人/远程用）
  video-digest bili-login --poll KEY 轮询某个二维码直到确认/过期

终端二维码和 PNG 需要 `pip install 'video-digest[bili]'`（qrcode 库）；没装时只打印链接。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
STATES = {0: "登录成功", 86101: "等待扫码", 86090: "已扫码，等待手机确认", 86038: "二维码已过期"}


def _get(url, jar=None):
    handlers = [urllib.request.HTTPCookieProcessor(jar)] if jar is not None else []
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    with urllib.request.build_opener(*handlers).open(req, timeout=15) as r:
        return json.loads(r.read().decode())


def generate_qr() -> dict:
    d = _get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate")["data"]
    out = {"qrcode_key": d["qrcode_key"], "url": d["url"], "png": None}
    try:
        import qrcode

        png = Path(tempfile.gettempdir()) / "bili-login-qr.png"
        qrcode.make(d["url"]).save(str(png))
        out["png"] = str(png)
    except ImportError:
        pass
    return out


def print_terminal_qr(url: str) -> None:
    try:
        import qrcode

        q = qrcode.QRCode(border=1)
        q.add_data(url)
        q.print_ascii(invert=True)
    except ImportError:
        print("（安装 qrcode 库可以直接在终端显示二维码：pip install 'video-digest[bili]'）")
    print(f"二维码链接（也可以把它转成二维码再扫）：{url}")


def poll(key: str, cookie_file: str, secs: int = 170) -> int:
    path = Path(cookie_file)
    jar = MozillaCookieJar(str(path))
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=" + urllib.parse.quote(key)
    end, last = time.time() + secs, None
    while time.time() < end:
        d = _get(url, jar)["data"]
        code = d.get("code")
        if code != last:
            print(STATES.get(code, code), flush=True)
            last = code
        if code == 0:
            # 有的环境 Set-Cookie 不落 jar，从回调 URL 兜底解析
            q = urllib.parse.parse_qs(urllib.parse.urlparse(d.get("url", "")).query)
            have = {c.name for c in jar}
            for k in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
                if k not in have and k in q:
                    jar.set_cookie(Cookie(0, k, q[k][0], None, False, ".bilibili.com", True, True, "/", True,
                                          k != "bili_jct", int(time.time()) + 180 * 86400, False, None, None, {}))
            path.parent.mkdir(parents=True, exist_ok=True)
            jar.save(ignore_discard=True, ignore_expires=True)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            print(f"cookie 已写入 {path}")
            return 0
        if code == 86038:
            return 2
        time.sleep(2)
    print("轮询超时")
    return 3


def check(cookie_file: str) -> dict:
    if not Path(cookie_file).exists():
        return {"isLogin": False, "error": f"cookie 文件不存在: {cookie_file}"}
    jar = MozillaCookieJar(cookie_file)
    jar.load(ignore_discard=True, ignore_expires=True)
    d = _get("https://api.bilibili.com/x/web-interface/nav", jar).get("data") or {}
    return {"isLogin": bool(d.get("isLogin")), "uname": d.get("uname"), "vip": d.get("vipStatus")}


def interactive(cookie_file: str) -> int:
    qr = generate_qr()
    print_terminal_qr(qr["url"])
    if qr["png"]:
        print(f"二维码图片：{qr['png']}")
    print("用手机 B站 App 扫码并确认（约 3 分钟内有效）...")
    return poll(qr["qrcode_key"], cookie_file)
