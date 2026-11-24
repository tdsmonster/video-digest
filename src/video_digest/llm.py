"""OpenAI 兼容的 Chat Completions 客户端（只用标准库）。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config


class LLMError(Exception):
    pass


def chat(cfg, prompt: str, max_tokens: int | None = None) -> str:
    llm = cfg["llm"]
    key = config.api_key(cfg)
    base = llm["base_url"].rstrip("/")
    # base_url 按 OpenAI SDK 的约定写（例如 https://api.openai.com/v1、http://localhost:11434/v1、https://api.deepseek.com）
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    body = {"model": llm["model"], "messages": [{"role": "user", "content": prompt}],
            "temperature": llm.get("temperature", 0.2), "max_tokens": max_tokens or llm.get("max_tokens", 16000)}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=llm.get("timeout", 300)) as r:
                data = json.loads(r.read().decode())
            content = data["choices"][0]["message"].get("content") or ""
            if not content.strip():
                raise LLMError("模型返回空内容（推理 token 可能占满了 max_tokens）")
            return content
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")[:300]
            last = f"HTTP {e.code}: {detail}"
            if e.code in (400, 401, 403, 404):        # 配置错误，重试没用
                break
        except Exception as e:
            last = str(e)
        time.sleep(3 * (attempt + 1))
    raise LLMError(f"LLM 调用失败（{llm['model']} @ {base}）: {last}")
