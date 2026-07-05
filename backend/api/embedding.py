"""
语义向量引擎

功能：
- 将文本转为向量（Embedding）
- 计算查询与商品之间的语义相似度
- 实现"模糊语义匹配"——买家说"我需要代步工具"能匹配到"自行车"

使用 SiliconFlow 的免费中文 Embedding API：
  注册：https://siliconflow.cn/
  模型：BAAI/bge-large-zh-v1.5（中文效果优秀，免费额度足够）
  兼容 OpenAI 接口格式，无需额外 SDK。

配置方式：在 .env 文件中设置
  SILICONFLOW_API_KEY=your_key

架构说明：
  本模块是提升搜索质量的关键。MVP 阶段即使不配置此模块，
  search_item.py 中的关键词匹配也能工作。加上此模块后，搜索
  质量会有显著提升，支持"模糊匹配"和"同义词理解"。
"""

import os
import math
import requests
from typing import Optional


# ── SiliconFlow Embedding ───────────────────────────

def embed_siliconflow(texts: list) -> Optional[list]:
    """
    调用 SiliconFlow BGE Embedding API 生成向量。

    文档：https://docs.siliconflow.cn/api-reference/embeddings/create-embeddings
    模型 BAAI/bge-large-zh-v1.5 为 1024 维，中文效果优秀。
    """
    api_key = os.environ.get("SILICONFLOW_API_KEY")
    if not api_key:
        return None

    url = "https://api.siliconflow.cn/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "BAAI/bge-large-zh-v1.5",
        "input": texts,
        "encoding_format": "float",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # 按输入顺序返回 embeddings
        embeddings = [item["embedding"] for item in data["data"]]
        return embeddings

    except Exception as e:
        print(f"[embedding] SiliconFlow Embedding 调用失败: {e}")
        return None


# ── 向量运算 ────────────────────────────────────────

def cosine_similarity(vec_a: list, vec_b: list) -> float:
    """计算两个向量的余弦相似度"""
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


# ── 语义搜索 ────────────────────────────────────────

def compute_similarity(query: str, item_texts: list, items: list) -> dict:
    """
    核心函数：计算查询文本与所有商品文本的语义相似度。

    参数：
        query: 买家查询（如"我需要一辆通勤用的自行车"）
        item_texts: 每个商品的搜索文本列表（由 search_item.py 构建）
        items: 商品对象列表（需要有 item_id 字段）

    返回：
        { "ITEM-xxx": 0.95, "ITEM-yyy": 0.82, ... }
        按 item_id 映射到相似度分数（0~1）

    说明：
        如果 API 调用失败，返回空 dict，search_item.py 会自动降级为关键词匹配。
    """
    if not query or not item_texts:
        return {}

    # 将所有文本合并为一个批处理（query 在第一项）
    all_texts = [query] + item_texts

    embeddings = embed_siliconflow(all_texts)

    if embeddings is None:
        return {}  # 降级到关键词匹配

    # 查询向量 = 第一项
    query_vec = embeddings[0]

    # 计算每个商品的相似度
    scores = {}
    for i, item in enumerate(items):
        item_vec = embeddings[i + 1]
        score = cosine_similarity(query_vec, item_vec)
        scores[item["item_id"]] = round(score, 4)

    return scores


# ── API 处理函数 ────────────────────────────────────

def handle_embed(texts: list) -> dict:
    """
    纯向量生成入口（供 main.py 的 /api/embed 端点调用）。

    输入：
        texts: ["文本1", "文本2", ...]

    输出：
        {
            "success": true,
            "embeddings": [[...], [...]],
            "dimension": 1024,
            "source": "siliconflow"
        }
    """
    if not texts:
        return {"success": False, "error": "请输入文本"}

    embeddings = embed_siliconflow(texts)

    if embeddings is None:
        return {"success": False, "error": "Embedding API 不可用，请检查 SILICONFLOW_API_KEY"}

    return {
        "success": True,
        "embeddings": embeddings,
        "dimension": len(embeddings[0]) if embeddings else 0,
        "source": "siliconflow",
    }
