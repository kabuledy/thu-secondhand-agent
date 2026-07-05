"""
商品搜索模块

功能：
- 接收自然语言查询文本
- 对查询进行意图解析（提取关键词、价格范围、品类等）
- 结合关键词匹配 + 语义向量检索，返回按符合度排序的商品列表

搜索策略（由粗到精）：
  第1层：关键词过滤 — 从数据库中找到所有在售商品
  第2层：语义排序 — 调用 Embedding API 计算文本相似度
  第3层：关键词加权 — 如果无法调用 Embedding API，降级为纯关键词 TF 排序

清小搭智能体调用示例：
  search_item("我需要一辆通勤用的自行车，预算300左右")
  → 返回排序后的商品列表
"""

import json
import os
import re
from typing import Optional

# 当 embedding 模块可用时导入，不可用时降级
try:
    from .embedding import compute_similarity
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False

from .list_item import get_active_items


# ── 查询解析 ────────────────────────────────────────

def parse_query(query: str) -> dict:
    """
    从自然语言查询中提取关键信息。

    输入："我需要一辆通勤用的自行车，预算300左右，蓝色的"
    输出：{
        "keywords": ["自行车", "通勤", "蓝色"],
        "price_max": 350,
        "price_min": 0,
        "category_hint": "交通工具",
        "clean_query": "通勤 自行车 蓝色"
    }
    """
    query_lower = query.lower()
    keywords = []

    # ── 提取价格 ──
    price_max = None
    price_min = None
    price_patterns = [
        (r"(?:预算|不超过|低于|以内|最多)[约]?(\d+)", "max"),
        (r"(?:高于|不低于|最少|起)[约]?(\d+)", "min"),
        (r"(\d+)[-~至到](\d+)", "range"),
        (r"[约]?(\d+)\s*[元块]", "max"),  # 模糊默认上限
    ]
    for pattern, ptype in price_patterns:
        match = re.search(pattern, query_lower)
        if match:
            if ptype == "max":
                price_max = int(match.group(1)) * 1.2  # 上下浮动20%
            elif ptype == "min":
                price_min = int(match.group(1))
            elif ptype == "range":
                price_min = int(match.group(1))
                price_max = int(match.group(2))

    # ── 提取关键词（去掉常见停用词） ──
    stop_words = {"我", "想", "要", "找", "一", "个", "些", "的", "了", "吗",
                  "吧", "啊", "呢", "有", "没", "在", "是", "不", "很", "太",
                  "和", "与", "或者", "大约", "左右", "预算", "以内", "价格",
                  "需要", "可以", "什么", "怎么", "这个", "那个", "还有"}

    # 分词（中文按字粒度 + 常见双字词提取）
    # 简单实现：按空格分离 + 2-4字连续汉字作为候选词
    tokens = re.findall(r'[一-鿿]{2,4}', query_lower)
    for token in tokens:
        if token not in stop_words and len(token) >= 2:
            keywords.append(token)

    # 去重
    keywords = list(dict.fromkeys(keywords))

    return {
        "keywords": keywords,
        "price_max": price_max,
        "price_min": price_min,
        "clean_query": " ".join(keywords),
    }


# ── 搜索实现 ────────────────────────────────────────

def keyword_score(item: dict, parsed: dict) -> float:
    """
    基于关键词的匹配得分（0~1）。

    匹配规则：
    - 名称命中关键词：+0.4/词
    - 描述命中关键词：+0.2/词
    - 标签命中关键词：+0.3/词
    - 品类命中关键词：+0.3/词
    """
    keywords = parsed.get("keywords", [])
    if not keywords:
        return 0.5  # 无关键词时给中间分

    score = 0.0
    total_weight = 0.0

    for kw in keywords:
        weight = 0.0
        if kw in item.get("name", "").lower():
            weight += 0.4
        if kw in item.get("description", "").lower():
            weight += 0.2
        if any(kw in tag.lower() for tag in item.get("tags", [])):
            weight += 0.3
        if kw in item.get("category", "").lower():
            weight += 0.3
        if kw in item.get("image_description", "").lower():
            weight += 0.1

        score += weight
        total_weight += 1.0

    return min(score / max(total_weight, 1.0), 1.0)


def price_score(item: dict, parsed: dict) -> float:
    """
    价格匹配得分（0~1）。
    如果用户没提预算，返回 1.0（不约束）。
    """
    item_price_str = item.get("price", "").strip()
    if not item_price_str:
        return 0.5  # 没标价格，给中间分

    # 尝试从商品价格中提取数字
    price_match = re.search(r'(\d+)', item_price_str)
    if not price_match:
        return 0.5
    item_price = int(price_match.group(1))

    price_max = parsed.get("price_max")
    price_min = parsed.get("price_min")

    if price_max is None and price_min is None:
        return 1.0  # 用户没提预算

    if price_max and price_min:
        if price_min <= item_price <= price_max:
            return 1.0
        else:
            return max(0, 1.0 - abs(item_price - (price_min + price_max) / 2) / max(price_max, 1))
    elif price_max:
        if item_price <= price_max:
            return 1.0
        else:
            return max(0, 1.0 - (item_price - price_max) / max(price_max, 1))
    elif price_min:
        if item_price >= price_min:
            return 1.0
        else:
            return max(0, 1.0 - (price_min - item_price) / max(price_min, 1))

    return 0.5


def freshness_score(item: dict) -> float:
    """
    新鲜度得分（0~1）。
    最近发布的商品获得更高分数，防止老商品永远霸榜。
    """
    from datetime import datetime, timezone

    try:
        created = datetime.fromisoformat(item.get("created_at", ""))
    except (ValueError, TypeError):
        return 0.5

    now = datetime.now(timezone.utc).replace(tzinfo=None) if created.tzinfo is None else datetime.now(timezone.utc)
    days_old = (now - created).days
    if days_old <= 1:
        return 1.0
    elif days_old <= 7:
        return 0.9
    elif days_old <= 14:
        return 0.7
    elif days_old <= 30:
        return 0.5
    else:
        return max(0.1, 1.0 - days_old / 180)


def search_items(query: str, top_k: int = 10) -> list:
    """
    主搜索函数。

    参数：
        query: 自然语言查询语句
        top_k: 返回 top N 条结果

    返回：
        [{
            "item_id": "...",
            "name": "...",
            "description": "...",
            "price": "...",
            "category": "...",
            "tags": [...],
            "image_description": "...",
            "score": 0.95,          # 综合匹配度 0~1
            "score_label": "95%",
            "created_at": "...",
        }, ...]
    """
    # 1. 获取所有在售商品
    all_items = get_active_items()
    if not all_items:
        return []

    # 2. 解析查询
    parsed = parse_query(query)

    # 3. 尝试语义向量检索（如果可用）
    semantic_scores = {}
    if EMBEDDING_AVAILABLE:
        try:
            # 为每个商品构建搜索文本
            item_texts = []
            for item in all_items:
                text = f"{item.get('name', '')} {item.get('description', '')} {' '.join(item.get('tags', []))} {item.get('category', '')} {item.get('image_description', '')}"
                item_texts.append(text)
            semantic_scores = compute_similarity(query, item_texts, all_items)
        except Exception:
            pass  # 降级到关键词匹配

    # 4. 计算综合得分
    scored_items = []
    for i, item in enumerate(all_items):
        kw_score = keyword_score(item, parsed)
        p_score = price_score(item, parsed)
        f_score = freshness_score(item)
        sem_score = semantic_scores.get(item["item_id"], 0.0) if semantic_scores else 0.0

        # 综合权重：语义 40% / 关键词 30% / 价格 15% / 新鲜度 15%
        if semantic_scores:
            total = sem_score * 0.40 + kw_score * 0.30 + p_score * 0.15 + f_score * 0.15
        else:
            total = kw_score * 0.55 + p_score * 0.20 + f_score * 0.25

        item_copy = dict(item)
        # 隐去联系方式（仅在 get_item_detail 中展示）
        # 联系方式已公开显示（用户要求展示全部信息）
        pass
        item_copy["score"] = round(total, 4)
        item_copy["score_label"] = f"{int(total * 100)}%"
        scored_items.append(item_copy)

    # 5. 排序 + 去低分
    scored_items.sort(key=lambda x: x["score"], reverse=True)
    scored_items = [it for it in scored_items if it["score"] > 0.05]

    return scored_items[:top_k]


# ── API 处理函数 ────────────────────────────────────

def handle_search_item(query: str) -> dict:
    """
    搜索入口，供 main.py 调用。

    输入：
        query: "我需要一辆通勤用的自行车"

    输出：
        {
            "success": true,
            "query": "...",
            "total": 3,
            "items": [...]
        }
    """
    if not query or not query.strip():
        return {"success": False, "error": "请输入搜索内容"}

    results = search_items(query.strip())

    return {
        "success": True,
        "query": query,
        "total": len(results),
        "items": results,
    }
