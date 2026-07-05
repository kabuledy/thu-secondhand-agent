"""
网络搜索模块

功能：
- 接收物品名称，搜索网络上的相关信息
- 用于帮助智能体生成商品介绍草稿
- 当卖家选择"AI帮我写介绍"时调用

搜索方案（按优先级）：
  1. DeepSeek 内置 web_search 工具（推荐，只需 DEEPSEEK_API_KEY）
  2. 兜底：返回通用模板提示

配置方式：在 .env 文件中设置
  DEEPSEEK_API_KEY=your_key
"""

import os
import json
import requests
from typing import Optional


# ── DeepSeek 联网搜索 ──────────────────────────────

def search_with_deepseek(query: str) -> Optional[list]:
    """
    通过 DeepSeek 的 web_search 工具进行搜索。

    文档：https://platform.deepseek.com/api-docs
    模型 deepseek-chat 支持 web_search 工具调用。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": f"请搜索关于「{query}」的用途、特点和常见信息，用中文回复，分条列出关键点"}
        ],
        "tools": [{"type": "web_search"}],
        "temperature": 0.3,
        "max_tokens": 1000,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        if not content:
            return None

        # 将返回内容拆分为结构化的片段
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        results = []
        for i, line in enumerate(lines):
            if len(line) > 10:  # 过滤太短的句子
                results.append({
                    "title": f"信息点 {i+1}",
                    "snippet": line,
                    "link": "",
                })

        return results if results else None

    except Exception as e:
        print(f"[web_search] DeepSeek 搜索失败: {e}")
        return None


# ── 兜底：返回通用信息模板 ─────────────────────────

def generate_fallback(query: str) -> list:
    """
    当所有搜索 API 都不可用时，返回一个引导性的模板。
    这样 LLM 仍然可以基于常识生成介绍。
    """
    return [
        {
            "title": f"关于「{query}」的常见信息",
            "snippet": f"{query}是一种常见的校园用品/设备。建议卖家补充以下信息：品牌、型号、购买时间、使用频率、有无损坏、附赠配件等。",
            "link": "",
            "_note": "兜底模板，非真实搜索结果",
        }
    ]


# ── API 处理函数 ────────────────────────────────────

def handle_web_search(query: str) -> dict:
    """
    网络搜索入口，供 main.py 调用。

    输入：
        query: "山地自行车 用途 特点"

    输出：
        {
            "success": true,
            "query": "...",
            "results": [
                {"title": "...", "snippet": "...", "link": "..."},
                ...
            ],
            "source": "deepseek"     // deepseek / fallback
        }
    """
    if not query or not query.strip():
        return {"success": False, "error": "请输入搜索内容"}

    query = query.strip()

    # 优先 DeepSeek 搜索
    results = search_with_deepseek(query)
    source = "deepseek"

    # 兜底
    if results is None:
        results = generate_fallback(query)
        source = "fallback"

    return {
        "success": True,
        "query": query,
        "results": results,
        "source": source,
    }
