"""
图片分析模块 — 物理成色感知引擎 (Physical Condition Sensing)

功能：
- analyze_with_qwen: 通用图片分析（Qwen-VL-Max，用于生成商品描述）
- assess_physical_condition: 物理成色量化评估（新增，实物感知核心）
  → 分析物品照片 → 输出量化 condition_score (0-100)
  → 数据直接喂给 PriceLearner 调整估值

底层视觉模型：阿里云通义千问 Qwen-VL-Max（DashScope API）
配置方式：在 .env 文件中设置 DASHSCOPE_API_KEY
  DASHSCOPE_API_KEY=your_key
"""

import os
import json
import base64
import requests
from typing import Optional


# ── 图片 URL 验证 ───────────────────────────────────

def is_valid_image_url(url: str) -> bool:
    """验证是否是有效的图片 URL（支持 http/https 和 base64 data URL）"""
    if not url:
        return False
    if url.startswith("data:image/"):
        return True
    if url.startswith(("http://", "https://")):
        return True
    return False


# ── DeepSeek 视觉模型（通用分析）──────────────────

def analyze_with_qwen(image_url: str) -> Optional[dict]:
    """
    调用 Qwen-VL-Max 视觉模型分析图片（通用分析）。
    用于生成商品描述文本。

    底层模型为阿里云通义千问 Qwen-VL-Max，OpenAI 兼容接口。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    api_result = _call_vision_api(image_url, _GENERAL_DESCRIPTION_PROMPT)
    return api_result


_GENERAL_DESCRIPTION_PROMPT = """请分析这张图片中的物品，以JSON格式返回以下信息：
{
    "category": "物品大类（如：交通工具、电子产品、书籍、家具、衣物、体育用品、生活用品、其他）",
    "name": "物品名称",
    "color": "主要颜色",
    "condition": "成色估算（全新/九成新/七成新/五成新/较旧）",
    "features": ["关键特征1", "关键特征2"],
    "description": "一段简短的中文描述（30字以内）"
}
只返回JSON，不要包含其他文字。"""


# ═══════════════════════════════════════════════════════════
# 🔬 核心升级：物理成色量化评估 (Physical Condition Sensing)
# ═══════════════════════════════════════════════════════════

_PHYSICAL_CONDITION_PROMPT = """你是一个二手物品鉴定专家。
请分析这张图片，以JSON格式返回以下信息：

{
    "is_sellable_item": true,
    "item_type_note": "如果is_sellable_item为false，简述这张图实际是什么内容（如：风景照、人物自拍、宠物、食物等）。为true时填空字符串。",
    "name": "物品名称（如：高等数学教材、蓝色山地自行车）",
    "category": "物品分类（书籍文具/数码/生活家居/服饰个护/运动出行/娱乐休闲）",
    "brand": "品牌/厂商（如：晨光、Nike、Apple），无法识别请填"未知"",
    "color": "主要颜色（从以下选择：红色/橙色/黄色/绿色/蓝色/紫色/粉色/黑白/灰色/棕色/银色/金色/透明/多色/其他）",
    "quantity": "数量规格（如：单件、一套6本、一盒12支），无法判断请填"未知"",
    "specs": "规格参数（如：A4大小、USB-C接口、500ml），无法判断请填"未知"",
    "style": "款式版型（如：精装/平装、翻盖/直板、长袖/短袖），无法判断请填"未知"",
    "physical_condition": {
        "overall_score": 85,
        "wear_level": "minor",
        "cleanliness": "good",
        "defects": [],
        "completeness": "complete",
        "color_fading": "none",
        "estimated_age_years": 1.0
    },
    "confidence": 0.90,
    "analysis_rationale": "书脊轻微磨损，内页无笔记，整体保存较好"
}

⚠️ 判断标准：
- is_sellable_item = true：书籍、数码产品、生活家居、服饰、运动器材、乐器、手办等
  适合在二手平台交易的物品
- is_sellable_item = false：风景照、人物/自拍、宠物、食物/饮品（已开封）、
  票据、截图、纯文字图片等不适合二手交易的**内容**

评分标准（仅is_sellable_item=true时有意义）：
- overall_score 0-100：0=废品, 20=严重损坏, 40=明显老旧, 60=正常使用痕迹,
  80=轻微使用痕迹, 95=近全新, 100=全新未拆
- wear_level: none(无磨损) / minor(轻微) / moderate(中等) / severe(严重)
- cleanliness: excellent(洁净) / good(较干净) / fair(一般) / poor(脏污)
- defects: 可见瑕疵列表（划痕、破损、污渍、缺页等），没有则填[]
- completeness: complete(完整) / missing_parts(缺配件) / damaged(部件损坏)
- color_fading: none(无褪色) / minor(轻微) / moderate(明显) / severe(严重褪色)
- confidence: 你对这个判断的整体把握度 0-1

⚠️ 对于你不确定的信息（品牌、规格、数量等），诚实地填"未知"，不要猜测。
只返回JSON，不要包含其他文字。"""


def assess_physical_condition(image_url: str) -> dict:
    """
    🔬 物理成色量化评估（Physical Sensing 核心函数）。

    分析物品照片，返回量化的物理成色评分（0-100）。
    这个评分会直接喂给 PriceLearner，影响估值计算。

    输入：
        image_url: 物品照片 URL

    输出：
        {
            "success": True,
            # ── PriceLearner 用 ──
            "condition_score": 85,          # 0-100 综合物理成色评分
            "condition_detail": {           # 成色详细维度
                "overall_score": 85,
                "wear_level": "minor",
                "cleanliness": "good",
                "defects": ["轻微划痕"],
                "completeness": "complete",
                "color_fading": "none",
                "estimated_age_years": 1.0
            },
            "vision_confidence": 0.88,      # 视觉判断置信度
            # ── DeepSeek 用 ──
            "description_data": {           # 物品综合信息（用于生成简介）
                "item_name": "高等数学教材",
                "category": "书籍文具",
                "brand": "未知",
                "color": "蓝色",
                "quantity": "单件",
                "specs": "A4大小",
                "style": "平装",
                "overall_condition_text": "轻微使用痕迹，较干净，配件完整",
                "defects": ["轻微划痕"],
                "analysis_rationale": "书脊轻微磨损，内页无笔记"
            },
            # ── 兼容字段 ──
            "item_name": "高等数学教材",
            "source": "deepseek"
        }

    如果视觉 API 不可用，返回基于默认值的保守估计：
        { "success": False, "condition_score": 70, "vision_confidence": 0.3 }
    """
    if not is_valid_image_url(image_url):
        return _default_condition("无效的图片 URL，使用默认成色估计")

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        return _default_condition("未配置 DashScope API Key，使用默认成色估计")

    result = _call_vision_api(image_url, _PHYSICAL_CONDITION_PROMPT)
    if result is None:
        return _default_condition("视觉 API 调用失败，使用默认成色估计")

    return _parse_condition_result(result, image_url)


def _default_condition(reason: str) -> dict:
    """
    兜底：当视觉 API 不可用时，返回保守的默认物理成色。
    默认假设物品为"正常二手"（score=70），置信度很低（0.3）。
    """
    return {
        "success": False,
        "condition_score": 70,
        "condition_detail": {
            "overall_score": 70,
            "wear_level": "moderate",
            "cleanliness": "fair",
            "defects": [],
            "color_fading": "moderate",
        },
        "vision_confidence": 0.3,
        "item_name": "",
        "source": "default",
        "_note": reason,
    }


def _parse_condition_result(api_result: dict, image_url: str) -> dict:
    """
    解析 Qwen-VL-Max 返回的物品鉴定 JSON。
    拆分为两路数据：
      - condition_data → PriceLearner 成色评估（内部算法用）
      - description_data → DeepSeek 生成物品简介（对外展示用）
    """
    try:
        # 先判断是不是可售卖物品
        is_sellable = api_result.get("is_sellable_item", True)
        # 类型转换确保 bool
        if isinstance(is_sellable, str):
            is_sellable = is_sellable.lower() in ("true", "yes", "1")
        is_sellable = bool(is_sellable)

        if not is_sellable:
            item_note = api_result.get("item_type_note", "")
            return {
                "success": True,
                "is_sellable_item": False,
                "item_type_note": item_note,
                "condition_score": 70,
                "vision_confidence": 0.3,
                "description_data": {
                    "item_name": f"非二手物品（{item_note}）" if item_note else "非二手物品",
                    "overall_condition_text": "",
                },
                "source": "deepseek",
            }

        pc = api_result.get("physical_condition", {})
        overall_score = pc.get("overall_score", 70)
        overall_score = max(0, min(100, int(overall_score)))

        vision_conf = api_result.get("confidence", 0.7)
        vision_conf = max(0.0, min(1.0, float(vision_conf)))

        # 描述数据（给 DeepSeek 生成简介用）
        description_data = {
            "item_name": api_result.get("name", "未知物品"),
            "category": api_result.get("category", ""),
            "brand": api_result.get("brand", "未知"),
            "color": api_result.get("color", "未知"),
            "quantity": api_result.get("quantity", "未知"),
            "specs": api_result.get("specs", "未知"),
            "style": api_result.get("style", "未知"),
            "overall_condition_text": _condition_to_text(
                pc.get("wear_level", "moderate"),
                pc.get("cleanliness", "fair"),
                pc.get("completeness", "complete"),
            ),
            "defects": pc.get("defects", []),
            "analysis_rationale": api_result.get("analysis_rationale", ""),
        }

        return {
            "success": True,
            "is_sellable_item": True,
            # ── 一路：PriceLearner 用（成色数据）──
            "condition_score": overall_score,
            "condition_detail": {
                "overall_score": overall_score,
                "wear_level": pc.get("wear_level", "moderate"),
                "cleanliness": pc.get("cleanliness", "fair"),
                "defects": pc.get("defects", []),
                "completeness": pc.get("completeness", "complete"),
                "color_fading": pc.get("color_fading", "moderate"),
                "estimated_age_years": pc.get("estimated_age_years", 1.0),
            },
            "vision_confidence": vision_conf,
            # ── 二路：DeepSeek 用（物品简介数据）──
            "description_data": description_data,
            # ── 兼容旧字段（外部可能直接读）──
            "item_name": api_result.get("name", ""),
            "source": "deepseek",
        }
    except Exception as e:
        print(f"[analyze_image] 解析物理成色结果失败: {e}")
        return _default_condition(f"解析视觉结果失败: {e}")


def _condition_to_text(wear_level: str, cleanliness: str, completeness: str) -> str:
    """将结构化成色数据转为一句自然语言描述，供 DeepSeek 在简介中使用。"""
    wear_map = {"none": "无明显磨损", "minor": "轻微使用痕迹",
                 "moderate": "正常使用痕迹", "severe": "明显磨损"}
    clean_map = {"excellent": "非常洁净", "good": "较干净",
                  "fair": "一般", "poor": "有污渍"}
    complete_map = {"complete": "配件完整", "missing_parts": "缺配件",
                     "damaged": "有部件损坏"}
    parts = [
        wear_map.get(wear_level, "正常使用痕迹"),
        clean_map.get(cleanliness, "较干净"),
    ]
    if completeness in complete_map:
        parts.append(complete_map[completeness])
    return "，".join(parts)


# ═══════════════════════════════════════════════════════════
# Qwen-VL-Max 视觉 API 调用（公共底层）
# ═══════════════════════════════════════════════════════════

_QWEN_VL_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
_QWEN_VL_MODEL = "qwen-vl-max"


def _image_to_base64_url(image_url: str) -> Optional[str]:
    """
    将图片 URL（http/https/data）转为 base64 data URL。
    Qwen-VL-Max 无法从外网 HTTP URL 下载图片，需要图片数据内嵌在请求中。
    """
    if image_url.startswith("data:image/"):
        return image_url  # 已经是 data URL，直接使用

    try:
        resp = requests.get(image_url, timeout=15, headers={
            "User-Agent": "THU-SecondHand-Agent/1.0",
        })
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        b64_str = base64.b64encode(resp.content).decode("ascii")
        return f"data:{content_type};base64,{b64_str}"
    except Exception as e:
        print(f"[analyze_image] 图片下载失败: {e}")
        return None


def _call_vision_api(image_url: str, prompt: str) -> Optional[dict]:
    """
    调用 Qwen-VL-Max 视觉模型（阿里云通义千问），返回解析后的 JSON dict。

    Qwen-VL-Max 擅长中文物品识别和物理成色判断。

    参数：
        image_url: 图片 URL（http 或 data:image）
        prompt:    JSON 格式要求的 prompt

    返回：
        dict（解析后的 JSON），或 None（失败时）
    """
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        return None

    # 将 HTTP URL 转为 base64 data URL（Qwen 无法从外网下载内网图片）
    data_url = _image_to_base64_url(image_url)
    if not data_url:
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": _QWEN_VL_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url}
                    }
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }

    try:
        resp = requests.post(_QWEN_VL_URL, headers=headers, json=payload, timeout=30)
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
        print(f"[analyze_image] Qwen-VL-Max API 调用失败: {e}")
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
    分析图片入口，供 main.py 调用（原通用分析，保持不变）。

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
    result = analyze_with_qwen(image_url)
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
