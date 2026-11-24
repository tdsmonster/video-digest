"""提示词。输出语言由 output.language 决定，提示词本身用中文写。"""

LANG_NAMES = {"zh": "简体中文", "zh-hant": "繁體中文", "en": "English", "ja": "日本語", "ko": "한국어"}


def lang_name(code: str) -> str:
    return LANG_NAMES.get((code or "zh").lower(), code)


def map_prompt(i: int, n: int, chunk: str) -> str:
    return (f"下面是一段视频转写（第 {i}/{n} 段），带 [时间戳]。请按时间顺序提炼要点，"
            f"每条保留原时间戳，保留具体数字、人名、结论和原话金句；不要评价、不要补充外部知识。\n\n{chunk}")


def digest_prompt(*, meta, dur_str, sources, weak, t_note, chapters, t_text, ocr_text, dm_text, language) -> str:
    weak_note = ("注意：本视频没有可靠的人声台词（" + t_note + "），内容只能依据画面文字、标题、简介和弹幕推断；"
                 "请在开头说明这一点，降低结论确定性，不要把歌词当成对白。") if weak else ""
    return f"""你在帮用户快速了解一个视频。只根据下面提供的材料写，材料里没有的内容不要编；不确定的地方标注"（推测）"。
先判断视频类型（教程/讲解、新闻评论、访谈、Vlog、游戏展示、音乐MV、广告等），按类型决定重点。
输出语言：{lang_name(language)}（标记行原样保留）。
{weak_note}

视频：《{meta['title']}》 作者：{meta['uploader']} 时长：{dur_str}
证据来源：{"；".join(sources) or "仅标题"}

请严格按以下两段输出：

<<<CARD>>>
（适合发到聊天软件的纯文本卡片，不要用 Markdown 符号如 ** # 表格；总长 ≤ 450 字）
第一行：一句话说清这个视频讲/展示了什么（≤60字）
然后 3–5 条要点，每条以「• 」开头，能对上时间的写 [mm:ss]
最后一行：「值不值得看：」+ 一句话判断（适合谁、可跳过哪段）
<<<END_CARD>>>

<<<REPORT>>>
# 《{meta['title']}》
（Markdown 长报告：内容概述 → 按时间顺序的结构/要点（带 [mm:ss]）→ 关键事实与数据 → 画面文字补充了什么 → 存疑点/证据不足之处。长度跟随信息量，不要注水。）
<<<END_REPORT>>>

【章节】
{chapters or "（无）"}

【作者简介】
{(meta.get("description") or "（无）")[:1500]}

【台词/转写】
{t_text or "（无可靠台词）"}

【画面文字（OCR，可能有错字）】
{ocr_text or "（无）"}

【弹幕高能时刻（观众反应，只说明哪里热闹/观众怎么看，不代表视频内容）】
{dm_text or "（无）"}
"""


def links_prompt(title: str, card: str, blocks: str) -> str:
    return f"""下面是视频《{title}》的摘要，以及作者在简介/置顶评论/画面里给出的链接及其内容。只依据给出的材料，不要编。

【视频摘要】
{card[:2500]}

【链接内容】
{blocks}

输出两段，标记原样保留：
<<<BRIEF>>>
每个链接一行，纯文本不用 Markdown 符号，格式「• [序号] 名称：是什么 / 和视频的关系 / 值不值得点开（一句话）」，总长 ≤ 350 字；抓取失败或未抓取的只写名称和原因
<<<END_BRIEF>>>
<<<DETAIL>>>
Markdown：对每个有内容的链接写 3–6 行要点；论文写清楚问题、方法、主要结论；GitHub 项目写用途、活跃度、上手门槛；最后一句说这些链接合起来对理解视频有什么帮助
<<<END_DETAIL>>>"""
