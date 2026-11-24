"""订阅追更：定时检查 B站收藏夹 / B站 UP 主 / 任意 yt-dlp 播放列表或频道，新视频自动解析。

每次运行：
  1. 逐个来源拉最新视频，和 SQLite 状态库比对找出新视频（来源第一次出现时，现有视频全部记为基线，不解析）
  2. 每个新视频：跑 digest（子进程，带超时）→ 从简介 / B站置顶评论 / 画面文字里提取链接 →
     arXiv 取摘要（可缓存 PDF）、GitHub 取仓库信息 + README、其他网页用 Jina Reader 取正文 → LLM 解读链接
  3. 每个视频写一份 Markdown 到 watch.output_dir；stdout 输出一份汇总（适合 cron 转发到 Telegram 等）
     没有新内容时 stdout 为空

配合 cron / launchd / Windows 任务计划程序使用。
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.cookiejar import MozillaCookieJar
from pathlib import Path

from . import llm, prompts
from .util import child_env, fmt_ts, log, run_cmd, ytdlp_cmd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
PER_VIDEO_TIMEOUT = 25 * 60
RUN_BUDGET_S = 45 * 60
MAX_LINKS = 6
SKIP_LINK = re.compile(r"pan\.baidu|quark\.cn|aliyundrive|alipan|lanzou|123pan|taobao|tmall|jd\.com|pinduoduo|"
                       r"space\.bilibili\.com|t\.bilibili\.com|bilibili\.com/opus|weixin\.qq\.com/r/|qm\.qq\.com|jq\.qq\.com")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Http:
    def __init__(self, cookie_file: str):
        self.jar = MozillaCookieJar(cookie_file)
        if Path(cookie_file).exists():
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, url, headers=None, timeout=20, raw=False, cookies=True):
        h = {"User-Agent": UA}
        if "bilibili.com" in url:
            h["Referer"] = "https://www.bilibili.com/"
        h.update(headers or {})
        op = self.op if cookies else urllib.request.build_opener()
        with op.open(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            data = r.read()
        return data if raw else data.decode("utf-8", errors="ignore")

    def bili(self, url):
        d = json.loads(self.get(url))
        if d.get("code") != 0:
            raise RuntimeError(f"B站接口 {d.get('code')}: {d.get('message')} ({url.split('?')[0]})")
        return d.get("data") or {}


# ------------------------------------------------------------------ 来源
def source_key(src: dict) -> str:
    return f"{src['type']}:{src.get('id') or src.get('url')}"


def list_source(http: Http, src: dict, pages: int = 1) -> list[dict]:
    t = src["type"]
    if t == "bili_fav":
        out = []
        for pn in range(1, pages + 1):
            d = http.bili(f"https://api.bilibili.com/x/v3/fav/resource/list?media_id={src['id']}&pn={pn}&ps=20&order=mtime&platform=web")
            for m in d.get("medias") or []:
                if m.get("type") == 2 and m.get("bvid") and m.get("title") != "已失效视频":
                    out.append({"id": m["bvid"], "title": m["title"], "url": f"https://www.bilibili.com/video/{m['bvid']}"})
            if not d.get("has_more"):
                break
        return out
    if t == "bili_up":
        mid = src["id"]
        d = json.loads(http.get(f"https://api.bilibili.com/x/v2/medialist/resource/list?mobi_app=web&type=1&biz_id={mid}&ps=20&direction=false",
                                headers={"Referer": f"https://space.bilibili.com/{mid}"}))
        items = (d.get("data") or {}).get("media_list") or []
        if d.get("code") != 0 or not items:
            raise RuntimeError(f"UP {mid} 列表接口失败: {d.get('code')} {d.get('message')}")
        return [{"id": m["bv_id"], "title": m["title"], "url": f"https://www.bilibili.com/video/{m['bv_id']}"}
                for m in items if m.get("bv_id")]
    if t == "ytdlp":       # YouTube 频道/播放列表等，任何 yt-dlp 支持的列表
        n = 200 if pages > 1 else 20
        c, out, err = run_cmd(ytdlp_cmd() + ["--flat-playlist", "-J", "--playlist-end", str(n), src["url"]], timeout=180)
        if c != 0:
            raise RuntimeError(f"yt-dlp 列表失败: {err.strip()[-200:]}")
        entries = (json.loads(out).get("entries") or [])
        res = []
        for e in entries:
            if e.get("_type") == "playlist":         # 频道首页会套一层 Videos/Shorts 标签页
                continue
            vid = e.get("id")
            if vid:
                res.append({"id": vid, "title": e.get("title") or vid, "url": e.get("url") or e.get("webpage_url") or vid})
        return res
    raise ValueError(f"未知来源类型 {t}（支持 bili_fav / bili_up / ytdlp）")


# ------------------------------------------------------------------ 状态库
def open_db(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE IF NOT EXISTS watch_items (
        source TEXT, vid TEXT, title TEXT, url TEXT, status TEXT, tries INTEGER DEFAULT 0,
        first_seen TEXT, done_at TEXT, doc_path TEXT, note TEXT, PRIMARY KEY (source, vid))""")
    return c


# ------------------------------------------------------------------ 链接
def bili_pinned_comment(http: Http, bvid: str) -> str:
    try:
        aid = http.bili(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")["aid"]
        d = http.bili(f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&next=0")
    except Exception as e:
        log(f"  置顶评论获取失败: {e}")
        return ""
    top = (d.get("top") or {}).get("upper") or (d.get("upper") or {}).get("top") or (d.get("top_replies") or [None])[0]
    if not top:
        return ""
    content = top.get("content") or {}
    text = content.get("message", "")
    for kw, v in (content.get("jump_url") or {}).items():
        u = v.get("pc_url") or (kw if kw.startswith("http") else "")
        if u:
            text += f"\n{u}"
    for r in top.get("replies") or []:
        if r.get("mid") == top.get("mid"):
            text += "\n" + (r.get("content") or {}).get("message", "")
    return text.strip()


def extract_links(text: str, self_id: str) -> list[str]:
    urls = re.findall(r"https?://[^\s　，。、）)\]】>\"'“”]+", text or "")
    urls += [f"https://www.bilibili.com/video/{b}" for b in re.findall(r"\b(BV[0-9A-Za-z]{10})\b", text or "") if b != self_id]
    seen, out = set(), []
    for u in urls:
        u = u.rstrip(".,;:!?")
        if "b23.tv" in u:
            try:
                with urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers={"User-Agent": UA}), timeout=10) as r:
                    u = r.geturl()
            except Exception:
                pass
        bv = re.search(r"bilibili\.com/video/(BV[0-9A-Za-z]{10})", u)
        key = u = f"https://www.bilibili.com/video/{bv.group(1)}" if bv else u
        key = key.rstrip("/")
        if (self_id and self_id in key) or key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out[:MAX_LINKS]


def screen_links(ocr_txt: str) -> list[str]:
    """画面上的网址：OCR 常漏 https:// 前缀、把 github 用户名拆开，修一下。"""
    ocr_txt = re.sub(r"github\.com\s*/\s*([\w.-]+(?:\s+[\w.-]+)*?)\s*/\s*([\w.-]+)",
                     lambda m: "github.com/" + m.group(1).replace(" ", "") + "/" + m.group(2), ocr_txt)
    found = re.findall(r"(?:https?://)?((?:github\.com/[\w.-]+/[\w.-]+)|(?:[a-z0-9-]+\.)+(?:com|cn|net|org|io|ai|dev|app)(?:/[\w./-]*)?)", ocr_txt)
    return ["https://" + u for u in found if not re.search(r"bilibili|b23|hdslb|youtube|youtu\.be", u) and not SKIP_LINK.search(u)]


class LinkFetcher:
    def __init__(self, http: Http, papers_dir: str):
        self.http, self.papers = http, Path(papers_dir) if papers_dir else None

    def arxiv(self, u):
        m = re.search(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5})", u)
        if not m:
            return None
        aid = m.group(1)
        page = self.http.get(f"https://arxiv.org/abs/{aid}", cookies=False)
        t = re.search(r'<h1 class="title mathjax"><span class="descriptor">Title:</span>(.*?)</h1>', page, re.S)
        a = re.search(r'class="abstract mathjax">(.*?)</blockquote>', page, re.S)
        title = html.unescape(re.sub(r"<.*?>", "", t.group(1))).strip() if t else f"arXiv:{aid}"
        abstract = html.unescape(re.sub(r"<.*?>", "", re.sub(r'<span class="descriptor">.*?</span>', "", a.group(1)))).strip() if a else ""
        extra = ""
        if self.papers:
            pdf = self.papers / f"{aid}.pdf"
            if not (pdf.exists() and pdf.stat().st_size > 10000):
                try:
                    data = self.http.get(f"https://arxiv.org/pdf/{aid}", raw=True, cookies=False, timeout=60)
                    if len(data) > 10000:
                        self.papers.mkdir(parents=True, exist_ok=True)
                        pdf.write_bytes(data)
                except Exception as e:
                    log(f"  PDF 下载失败 {aid}: {e}")
            extra = str(pdf) if pdf.exists() else ""
        return {"kind": "论文", "title": title, "text": f"arXiv:{aid}\n摘要：{abstract}", "extra": extra}

    def github(self, u):
        m = re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+)", u)
        if not m:
            return None
        owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2))
        hdr = {"Accept": "application/vnd.github+json"}
        try:
            info = json.loads(self.http.get(f"https://api.github.com/repos/{owner}/{repo}", headers=hdr, cookies=False))
        except Exception:
            # OCR/手打地址常有错：按仓库名搜索，优先 owner 前缀吻合的
            q = urllib.parse.quote(f"{repo} in:name")
            items = json.loads(self.http.get(f"https://api.github.com/search/repositories?q={q}&per_page=10",
                                             headers=hdr, cookies=False)).get("items") or []
            hit = next((i for i in items if i["name"].lower() == repo.lower() and
                        i["owner"]["login"].lower().startswith(owner.lower()[:4])), None) or \
                next((i for i in items if i["name"].lower() == repo.lower()), None)
            if not hit:
                raise
            info, owner, repo = hit, hit["owner"]["login"], hit["name"]
        readme = ""
        try:
            readme = self.http.get(f"https://api.github.com/repos/{owner}/{repo}/readme",
                                   headers={"Accept": "application/vnd.github.raw"}, cookies=False)[:3500]
        except Exception:
            pass
        meta = (f"{owner}/{repo}：{info.get('description') or ''}\n⭐{info.get('stargazers_count')} · "
                f"最近提交 {str(info.get('pushed_at'))[:10]} · {info.get('language') or ''} · "
                f"许可 {(info.get('license') or {}).get('spdx_id', '无')}")
        return {"kind": "GitHub", "title": f"{owner}/{repo}", "text": meta + "\nREADME 节选：\n" + readme, "extra": ""}

    def bili_video(self, u):
        m = re.search(r"(BV[0-9A-Za-z]{10})", u)
        if not m:
            return None
        v = self.http.bili(f"https://api.bilibili.com/x/web-interface/view?bvid={m.group(1)}")
        return {"kind": "B站视频", "title": v.get("title", m.group(1)),
                "text": f"UP：{(v.get('owner') or {}).get('name', '')}；简介：{(v.get('desc') or '')[:400]}", "extra": ""}

    def web(self, u):
        body = self.http.get(f"https://r.jina.ai/{u}", headers={"User-Agent": "video-digest"}, cookies=False, timeout=30)
        if len(body) < 100 or "Captcha" in body[:500]:
            return None
        t = re.search(r"^Title:\s*(.+)$", body, re.M)
        return {"kind": "网页", "title": (t.group(1).strip() if t else u)[:80], "text": body[:4000], "extra": ""}

    def fetch(self, u):
        if SKIP_LINK.search(u):
            return {"url": u, "kind": "未抓取", "title": u, "text": "网盘/商品/社交主页类链接，未抓取", "extra": ""}
        try:
            fn = self.arxiv if "arxiv.org" in u else self.github if "github.com" in u else \
                self.bili_video if "bilibili.com/video" in u else self.web
            r = fn(u) or (self.web(u) if fn is not self.web else None)
            if r:
                r["url"] = u
                return r
        except Exception as e:
            return {"url": u, "kind": "抓取失败", "title": u, "text": str(e)[:200], "extra": ""}
        return {"url": u, "kind": "抓取失败", "title": u, "text": "无正文", "extra": ""}


def analyze_links(cfg, title, card, links):
    fetched = [l for l in links if l["kind"] not in ("未抓取", "抓取失败")]
    if not fetched:
        brief = "\n".join(f"• {l['url']}（{l['kind']}）" for l in links)
        return brief, brief
    blocks = "\n\n".join(f"[{i}] 类型：{l['kind']}｜{l['title']}\nURL：{l['url']}\n{l['text']}" for i, l in enumerate(links, 1))
    raw = llm.chat(cfg, prompts.links_prompt(title, card, blocks), 12000)
    b = re.search(r"<<<BRIEF>>>([\s\S]*?)<<<END_BRIEF>>>", raw)
    d = re.search(r"<<<DETAIL>>>([\s\S]*?)(?:<<<END_DETAIL>>>|$)", raw)
    brief = re.sub(r"\*\*|^#+\s*", "", (b.group(1) if b else "").strip(), flags=re.M)
    return brief or "（链接解读解析失败，见报告）", (d.group(1).strip() if d else raw)


# ------------------------------------------------------------------ 单个视频
def process_item(cfg, http: Http, item: dict, label: str, mode: str, config_path: str | None) -> dict:
    w = cfg["watch"]
    work = Path(cfg["output"]["dir"]) / f"watch_{re.sub(r'[^0-9A-Za-z_-]', '', item['id'])[:40]}"
    log(f"→ 解析 {item['id']}（{label}）")
    t0 = time.time()
    cmd = [sys.executable, "-m", "video_digest", "run", item["url"], "-o", str(work), "--json", "--mode", mode]
    if config_path:
        cmd += ["--config", config_path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=PER_VIDEO_TIMEOUT, env=child_env())
        code, err_tail = p.returncode, (p.stderr or "").strip()[-300:]
    except subprocess.TimeoutExpired:
        code, err_tail = -1, "解析超时"
    res_file = work / "result.json"
    if code not in (0, 3) or not res_file.exists():
        st = "unavailable" if code == 1 else ("noevidence" if code == 2 and "证据不足" in err_tail else "error")
        return {"status": st,
                "note": err_tail or f"digest 退出码 {code}"}
    res = json.loads(res_file.read_text(encoding="utf-8"))
    card = (work / "summary.txt").read_text(encoding="utf-8")
    report = (work / "report.md").read_text(encoding="utf-8")
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))

    pinned, links, link_brief, link_detail = "", [], "", ""
    if w.get("fetch_links", True):
        is_bili = item["id"].startswith("BV")
        pinned = bili_pinned_comment(http, item["id"]) if is_bili else ""
        ocr_file = work / "ocr.txt"
        screen = screen_links(ocr_file.read_text(encoding="utf-8")) if ocr_file.exists() else []
        src_text = (meta.get("description") or "") + "\n" + pinned + "\n" + "\n".join(screen)
        fetcher = LinkFetcher(http, w.get("papers_dir") or "")
        links = [fetcher.fetch(u) for u in extract_links(src_text, item["id"])]
        if links:
            log(f"  链接 {len(links)} 个：" + ", ".join(l["kind"] for l in links))
            try:
                link_brief, link_detail = analyze_links(cfg, meta.get("title", item["id"]), card, links)
            except Exception as e:
                link_brief = "\n".join(f"• {l['title']}（{l['kind']}）" for l in links)
                link_detail = f"链接解读失败：{e}\n\n" + link_brief

    out = Path(w["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    doc = out / f"{datetime.now():%Y-%m-%d}-{re.sub(r'[^0-9A-Za-z_-]', '', item['id'])}.md"
    papers = [l["extra"] for l in links if l.get("extra")]
    doc.write_text(f"""# {meta.get('title', item['id'])}

- 来源：{label}
- 视频：{item['url']}
- 作者：{meta.get('uploader', '')} · 时长 {fmt_ts(meta.get('duration') or 0)}
- 解析：{res.get('mode')} 模式；{res.get('transcript_source')}；画面文字 {res.get('ocr_items')} 条；弹幕 {res.get('danmaku_count')} 条
- 归档：{now()}
{"- 论文 PDF：" + "、".join(papers) if papers else ""}

## 摘要卡片
{card}

{report}

## 简介与置顶评论中的链接
{link_detail or "（无链接）"}

### 原始链接
{chr(10).join(f"- [{l['kind']}] {l['title']} — {l['url']}" for l in links) or "（无）"}

### 置顶评论原文
{pinned or "（无置顶评论 / 非 B站）"}
""", encoding="utf-8")
    log(f"  完成 {time.time() - t0:.0f}s → {doc}")
    return {"status": "done", "doc": str(doc), "card": card, "links": link_brief, "title": meta.get("title", item["id"])}


# ------------------------------------------------------------------ main
def run(cfg, *, init=False, dry_run=False, only=None, max_n=None, mode=None, config_path=None) -> int:
    w = cfg["watch"]
    srcs = w.get("sources") or []
    if not srcs:
        log("没有配置任何来源：在 config.toml 里加 [[watch.sources]]（见 config.example.toml）")
        return 1
    http = Http(cfg["bilibili"]["cookies"])
    if any(s["type"] == "bili_fav" for s in srcs) and not Path(cfg["bilibili"]["cookies"]).exists():
        print("⚠️ 没有 B站登录 cookie，私密收藏夹读不到：先运行 video-digest bili-login")
    c = open_db(w["state_db"])
    problems = []
    max_n = max_n or int(w.get("max_per_run") or 5)
    mode = mode or w.get("mode") or "auto"

    for src in srcs:
        key, label = source_key(src), src.get("label") or source_key(src)
        known = {r[0] for r in c.execute("SELECT vid FROM watch_items WHERE source=?", (key,))}
        try:   # 基线要覆盖全部现有视频（否则取消收藏后旧视频顶上来会被当成新的）
            items = list_source(http, src, pages=10 if (init or not known) else 1)
        except Exception as e:
            problems.append(f"{label} 列表获取失败：{e}")
            continue
        if init or (not known and not only):
            for it in items:
                c.execute("INSERT OR IGNORE INTO watch_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (key, it["id"], it["title"], it["url"], "baseline", 0, now(), None, None, "基线"))
            c.commit()
            log(f"[基线] {label}：登记 {len(items)} 个现有视频（不解析）")
            continue
        for it in items:
            if it["id"] not in known:
                c.execute("INSERT OR IGNORE INTO watch_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (key, it["id"], it["title"], it["url"], "pending", 0, now(), None, None, None))
        c.commit()
    if init:
        return 0

    if only:
        rows = c.execute("SELECT source, vid, title, url FROM watch_items WHERE vid=?", (only,)).fetchall() or \
            [(source_key(srcs[0]), only, only, only)]
    else:
        rows = c.execute("SELECT source, vid, title, url FROM watch_items WHERE status IN ('pending','error') AND tries < 3 "
                         "ORDER BY first_seen").fetchall()
    if dry_run:
        for r in rows:
            log(f"待处理: {r[1]} {r[2]}")
        return 0

    labels = {source_key(s): s.get("label") or source_key(s) for s in srcs}
    results, start = [], time.monotonic()
    for i, (key, vid, title, url) in enumerate(rows):
        if i >= max_n or time.monotonic() - start > RUN_BUDGET_S:
            break
        label = labels.get(key, key)
        r = process_item(cfg, http, {"id": vid, "title": title, "url": url}, label, mode, config_path)
        st = r["status"]
        c.execute("UPDATE watch_items SET status=?, tries=tries+1, done_at=?, doc_path=?, note=? WHERE source=? AND vid=?",
                  (st, now() if st != "error" else None, r.get("doc"), r.get("note"), key, vid))
        c.commit()
        r.update(vid=vid, url=url, label=label, title=r.get("title") or title)
        results.append(r)
    left = len(rows) - len(results)

    if not results and not problems:
        return 0
    done = [r for r in results if r["status"] == "done"]
    out = [f"📺 订阅更新 · {datetime.now():%m-%d %H:%M} · 新视频 {len(done)} 个"]
    for label in dict.fromkeys(r["label"] for r in results):
        out.append(f"\n━━ {label}")
        for r in (x for x in results if x["label"] == label):
            if r["status"] == "done":
                out.append(r["card"].strip())
                if r.get("links"):
                    out.append("🔗 相关链接：\n" + r["links"].strip())
                out.append(f"▶ {r['url']}\n")
            else:
                why = {"unavailable": "视频不可用", "noevidence": "证据不足，未生成摘要", "error": "解析出错，下次重试"}[r["status"]]
                out.append(f"《{r['title']}》{why}：{(r.get('note') or '')[-120:]}\n▶ {r['url']}\n")
    if left > 0:
        out.append(f"（还有 {left} 个排队，下次继续）")
    if problems:
        out.append("⚠️ " + "；".join(problems))
    if done:
        out.append(f"完整报告：{w['output_dir']}")
    print("\n".join(out))
    return 0
