"""
图片分析模块

功能：
- 接收卖家上传的物品照片 URL
- 调用 DeepSeek 视觉模型分析图片
- 返回：物品类别、颜色、成色估算、文字描述

配置方式：在 .env 文件中设置 DEEPSEEK_API_KEY
  DEEPSEEK_API_KEY=your_key

DeepSeek 视觉模型兼容 OpenAI 接口格式，支持图片理解。
"""

import os
import json
import requests
from typing import Optional


# ── 图片 URL 验证 ───────────────────────────────────

def is_valid_image_url(url: str) -> bool:
    """简单验证是否是有效的图片 URL"""
    if not url or not url.startswith(("http://", "https://")):
        return False
    return True


# ── DeepSeek 视觉模型 ──────────────────────────────

def analyze_with_deepseek(image_url: str) -> Optional[dict]:
    """
    调用 DeepSeek 视觉模型分析图片。

    API 文档：https://platform.deepseek.com/api-docs
    模型 deepseek-chat 支持图片理解（多模态输入）。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    prompt = """请分析这张图片中的物品，以JSON格式返回以下信息：
{
    "category": "物品大类（如：交通工具、电子产品、书籍、家具、衣物、体育用品、生活用品、其他）",
    "name": "物品名称",
    "color": "主要颜色",
    "condition": "成色估算（全新/九成新/七成新/五成新/较旧）",
    "features": ["关键特征1", "关键特征2"],
    "description": "一段简短的中文描述（30字以内）"
}
只返回JSON，不要包含其他文字。"""

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url}
                    }
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]

        # 清理返回内容，有时模型会返回 markdown 包裹的 JSON
        content_clean = content.strip()
        if content_clean.startswith("```"):
            content_clean = content_clean.split("\n", 1)[-1]
            content_clean = content_clean.rsplit("```", 1)[0]
        content_clean = content_clean.strip()

        result = json.loads(content_clean)
        return result

    except Exception as e:
        print(f"[analyze_image] DeepSeek API 调用失败: {e}")
        return None


# ── 兜底方案：无 API 时的启发式分析 ─────────────────

def heuristic_analysis(image_url: str) -> dict:
    """
    当所有视觉 API 都不可用时，返回基于 URL 的启发式结果。
    这个很简陋，只是保证系统不崩溃。
    """
    url_lower = image_url.lower()
    filename = url_lower.split("/")[-1].split("?")[0]

    return {
        "category": "其他",
        "name": filename or "未知物品",
        "color": "未知",
        "condition": "未知",
        "features": [],
        "description": f"已收到图片（{filename}），建议用户自行补充描述",
        "_note": "启发式分析（未调用视觉 API），仅供参考",
    }


# ── API 处理函数 ────────────────────────────────────

def handle_analyze_image(image_url: str) -> dict:
    """
    分析图片入口，供 main.py 调用。

    输入：
        image_url: "https://example.com/photo.jpg"

    输出：
        {
            "success": true,
            "analysis": { ... },
            "source": "deepseek"      // deepseek / heuristic
        }
    """
    if not is_valid_image_url(image_url):
        return {
            "success": False,
            "error": "无效的图片 URL，请提供有效的网络图片链接",
        }

    # 优先 DeepSeek
    result = analyze_with_deepseek(image_url)
    source = "deepseek"

    # 兜底
    if result is None:
        result = heuristic_analysis(image_url)
        source = "heuristic"

    return {
        "success": True,
        "analysis": result,
        "source": source,
    }
