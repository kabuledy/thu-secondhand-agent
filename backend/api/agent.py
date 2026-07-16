"""
清小闲智能体 — OpenAI 兼容的 Chat Completions 引擎

功能：
- 实现 OpenAI 兼容的 /v1/chat/completions 接口（清小搭「标准协议接入」要求）
- 内置系统提示词（prompt/system_prompt.md），不依赖平台侧 LLM
- 通过 DeepSeek 函数调用（Function Calling）自动路由到 6 个工具
- 支持流式（SSE）和非流式响应
- 自动处理多轮工具调用（最多 10 轮防无限循环）
"""

import os
import json
import time
import requests
from typing import Generator, List, Dict, Any, Optional


# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
MAX_TOOL_CALLS = 10

# 议价模拟数据模块
try:
    from .bargain_data import (
        record_sim_deal, record_sim_fail, record_real_deal,
        get_bargain_stats, get_global_stats, init_bargain_table
    )
    from .price_learning import learner_from_db_record
    _BARGAIN_AVAILABLE = True
except ImportError as e:
    print(f"[agent] 议价模块未加载: {e}")
    _BARGAIN_AVAILABLE = False


# ═══════════════════════════════════════════════════════════
# System Prompt 加载
# ═══════════════════════════════════════════════════════════

def load_system_prompt() -> str:
    """从 prompt/system_prompt.md 加载系统提示词"""
    prompt_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "prompt", "system_prompt.md"
    )
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            return content
    except FileNotFoundError:
        return "你是清小闲（TsingHua Second-Hand Assistant），清华大学校园二手智能助手。请用中文回答。"


# ═══════════════════════════════════════════════════════════
# OpenAI 兼容的工具定义（6 个工具）
# ═══════════════════════════════════════════════════════════

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_item",
            "description": "发布一个新商品。必填：名称、价格、联系方式、标签。描述可由AI生成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "物品名称，如'蓝色山地自行车'、'全新微积分教材'"
                    },
                    "description": {
                        "type": "string",
                        "description": "物品详细描述，含品牌、新旧程度、购买时间等"
                    },
                    "contact_type": {
                        "type": "string",
                        "enum": ["wechat", "phone", "email", "in_person"],
                        "description": "联系方式类型：wechat(微信) / phone(手机) / email(邮箱) / in_person(当面)"
                    },
                    "contact_value": {
                        "type": "string",
                        "description": "联系方式具体值，如微信号、手机号、邮箱地址"
                    },
                    "price": {
                        "type": "string",
                        "description": "价格或价格范围，如'300'、'200-350'"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签列表，至少1个，最多5个，如['自行车','通勤']"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["书籍文具", "数码", "生活家居", "服饰个护", "运动出行", "娱乐休闲"],
                        "description": "物品分类（必填）。书籍文具（教材/小说/笔/本）、数码（手机/耳机/充电宝）、生活家居（台灯/椅子/床垫/风扇）、服饰个护（衣服/鞋/护肤品/按摩仪）、运动出行（球拍/自行车）、娱乐休闲（吉他/桌游）"
                    },
                    "seller_min_price": {
                        "type": "string",
                        "description": "卖家能接受的最低价格（可选）。用于模拟讨价还价功能，仅AI可见，不会展示给买家。如果用户不愿意提供，传空字符串或忽略。"
                    },
                    "user_confirmed": {
                        "type": "boolean",
                        "description": "⚠️ 安全字段：用户是否已明确确认发布？必须在用户看了汇总信息后说了'确认'或'确认发布'或明确同意时才能设为true。绝对不能设为true如果你自己编造了任何信息。如果用户只提供了部分信息、或者你无法确认用户是否同意，设为false。"
                    }
                },
                "required": ["name", "description", "price", "contact_type", "contact_value", "tags", "category", "user_confirmed"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_item",
            "description": "根据自然语言描述搜索商品。支持模糊语义匹配，返回按匹配度排序的结果。比如搜'代步工具'能匹配到'自行车'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询语句，如'我需要一辆通勤用的自行车，预算300'"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "通过 DeepSeek 联网搜索获取网络信息。主要用于卖家选择'AI帮我写介绍'时，搜索物品的常见用途和特点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如'山地自行车 用途 特点 校园'"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_status",
            "description": "更新商品状态：sold(已售出) / deleted(已下架)。用户说'卖掉了'或'下架'时调用。如果用户选择已售出，必须询问最终成交价并传入final_price。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "商品ID"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["sold", "deleted"],
                        "description": "sold=已售出 / deleted=下架不再出售"
                    },
                    "final_price": {
                        "type": "number",
                        "description": "最终成交价（元）。status=sold时必填，必须用户亲口提供。status=deleted时不填。"
                    }
                },
                "required": ["item_id", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_popular_tags",
            "description": "获取当前使用频率最高的标签及其使用次数（最多3个，不足3个有多少返回多少）。用于开场时向用户推荐热门标签，或在无搜索结果时推荐其他类别。无标签时返回空列表。",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_by_tag",
            "description": "按标签搜索商品，返回所有带有该标签的在售物品列表。支持模糊匹配：搜'书'可以找到带'教材'、'小说'标签的物品。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "标签名称，如'文具'、'书'、'生活用品'"
                    }
                },
                "required": ["tag"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_item_detail",
            "description": "获取单个商品的完整真实信息，包括联系方式、价格、描述等。用户说'详情1'、'看看这个按摩仪'时调用。传入item_id或item_name均可，至少填一个。必须使用此工具获取数据，不能自己编造。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "商品编号，如 ITEM-20260716-85ED。知道编号时用这个。"
                    },
                    "item_name": {
                        "type": "string",
                        "description": "商品名称，如'按摩仪'。不知道编号只知道商品名时用这个，工具会自动搜索。"
                    }
                },
                "anyOf": [{"required": ["item_id"]}, {"required": ["item_name"]}]
            }
        }
    },
    # ── 模拟讨价还价工具 ──
    {
        "type": "function",
        "function": {
            "name": "record_bargain_outcome",
            "description": "⭐ 模拟讨价还价：记录一次模拟议价的结果（成交或未成交）。成交时传deal_price，未成交时传buyer_offer/seller_counter/reason。系统会自动累加统计数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "商品编号"
                    },
                    "outcome": {
                        "type": "string",
                        "enum": ["deal", "fail"],
                        "description": "deal=成交 / fail=未成交"
                    },
                    "deal_price": {
                        "type": "number",
                        "description": "成交价（outcome=deal时必填）"
                    },
                    "buyer_offer": {
                        "type": "number",
                        "description": "买家最后出价（outcome=fail时必填）"
                    },
                    "seller_counter": {
                        "type": "number",
                        "description": "卖家最后还价（outcome=fail时可选）"
                    },
                    "reason": {
                        "type": "string",
                        "enum": ["buyer_declined", "seller_rejected", "timeout"],
                        "description": "未成交原因：buyer_declined(买家放弃) / seller_rejected(卖家拒绝) / timeout(超时)"
                    }
                },
                "required": ["item_id", "outcome"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_bargain_insights",
            "description": "⭐ 模拟讨价还价（仅供AI内部参考，不展示给用户）：获取某个商品的议价数据分析。返回平均成交价、建议出价区间、算法置信度等信息，供AI内部决定如何回应议价时使用，所有数据不得向用户展示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "商品编号"
                    }
                },
                "required": ["item_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "report_real_transaction",
            "description": "⭐ 模拟讨价还价：用户在真实线下交易后，回来报告最终成交价。系统会记录真实价格并与模拟数据对比，不断优化算法。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "商品编号"
                    },
                    "final_price": {
                        "type": "number",
                        "description": "最终成交价（元）"
                    }
                },
                "required": ["item_id", "final_price"]
            }
        }
    }
]


# ═══════════════════════════════════════════════════════════
# 工具执行器
# ═══════════════════════════════════════════════════════════

TOOL_HANDLERS = {}

# 议价模块的handler引用（延迟填充）
_BARGAIN_HANDLERS_REGISTERED = False
_current_conv_id: Optional[str] = None


def _register_handlers():
    """延迟导入工具处理器（避免循环依赖）"""
    from .list_item import handle_list_item
    from .search_item import handle_search_item
    from .web_search import handle_web_search
    from .tag_utils import get_popular_tags as _get_popular_tags, search_by_tag as _search_by_tag
    from .database import update_item_status as _db_update_status, get_item as _db_get_item

    def _handle_status(args: dict) -> dict:
        """更新商品状态：下架或已售"""
        item_id = args.get("item_id", "")
        status = args.get("status", "")
        final_price = args.get("final_price")

        if status not in ("sold", "deleted"):
            return {"success": False, "error": "状态无效，请用 sold(已售出) 或 deleted(已下架)"}

        item = _db_get_item(item_id)
        if not item:
            return {"success": False, "error": "商品不存在"}

        # 如果已售出，校验成交价
        if status == "sold":
            if final_price is None or final_price <= 0:
                return {"success": False, "error": "已售出必须提供最终成交价（final_price），请询问用户成交金额"}
            # 级联：更新商品状态 + 记录真实成交价 + 清理标签
            ok = _db_update_status(item_id, "sold")
            if not ok:
                return {"success": False, "error": "状态更新失败"}

            # 记录真实成交价到议价数据
            bargain_msg = ""
            try:
                from .bargain_data import record_real_deal
                deal_result = record_real_deal(item_id, float(final_price))
                if deal_result.get("success"):
                    bargain_msg = deal_result.get("message", "")
            except Exception as e:
                bargain_msg = f"（议价记录异常: {e}）"

            # 清理该商品的标签统计
            try:
                from .database import remove_tags
                tags = item.get("tags", [])
                if tags:
                    remove_tags(tags)
            except Exception:
                pass

            msg = f"✅ 已标记为已售出，最终成交价 ¥{final_price}。{bargain_msg}"
            return {"success": True, "status": "sold", "final_price": final_price, "message": msg}

        # 下架（不再出售）
        else:
            ok = _db_update_status(item_id, "deleted")
            if not ok:
                return {"success": False, "error": "更新失败"}

            # 清理该商品的标签统计
            try:
                from .database import remove_tags
                tags = item.get("tags", [])
                if tags:
                    remove_tags(tags)
            except Exception:
                pass

            return {"success": True, "status": "deleted", "message": "商品已下架，不再对外展示。"}

    # ── get_item_detail 处理器 ──
    def _handle_get_detail(args: dict) -> dict:
        item_id = args.get("item_id", "")
        item_name = args.get("item_name", "")

        # 如果传了名称但没有编号，搜索匹配
        if not item_id and item_name:
            from .search_item import handle_search_item
            search_result = handle_search_item(item_name)
            if search_result.get("success") and search_result.get("items"):
                # 取最匹配的第一个
                return {"success": True, "item": search_result["items"][0]}
            return {"success": False, "error": f"没有找到叫「{item_name}」的商品"}

        # 按编号查找
        item = _db_get_item(item_id)
        if not item:
            return {"success": False, "error": "商品不存在或已下架"}
        if item.get("status") != "active":
            return {"success": False, "error": "该商品已下架或已售出"}
        return {"success": True, "item": item}

    TOOL_HANDLERS.update({
        "list_item": lambda args: handle_list_item(args),
        "search_item": lambda args: handle_search_item(args.get("query", "")),
        "web_search": lambda args: handle_web_search(args.get("query", "")),
        "update_status": lambda args: _handle_status(args),
        "get_item_detail": lambda args: _handle_get_detail(args),
        "get_popular_tags": lambda args: {"success": True, "tags": _get_popular_tags(3)},
        "search_by_tag": lambda args: _handle_search_by_tag(args, _search_by_tag),
    })

    # ── 注册议价工具 handlers ──
    _register_bargain_handlers(TOOL_HANDLERS, _db_get_item)


def _handle_search_by_tag(args: dict, search_by_tag) -> dict:
    tag = args.get("tag", "").strip()
    if not tag:
        return {"success": False, "error": "请输入标签"}
    items = search_by_tag(tag)
    return {"success": True, "tag": tag, "total": len(items), "items": items}


def _register_bargain_handlers(handler_dict: dict, db_get_item):
    """
    注册模拟讨价还价的工具处理器。
    如果议价模块不可用，注册降级处理器。
    """
    global _BARGAIN_HANDLERS_REGISTERED
    if _BARGAIN_HANDLERS_REGISTERED:
        return
    _BARGAIN_HANDLERS_REGISTERED = True

    # 初始化议价数据表（幂等操作）
    try:
        init_bargain_table()
    except Exception as e:
        print(f"[agent] 议价数据表初始化失败: {e}")

    if not _BARGAIN_AVAILABLE:
        # 议价模块不可用时的降级处理
        handler_dict.update({
            "record_bargain_outcome": lambda args: {
                "success": False,
                "error": "议价模块未加载，请联系管理员"
            },
            "get_bargain_insights": lambda args: {
                "success": False,
                "error": "议价模块未加载，请联系管理员"
            },
            "report_real_transaction": lambda args: {
                "success": False,
                "error": "议价模块未加载，请联系管理员"
            },
        })
        return

    # ── 1. record_bargain_outcome ──
    def _handle_record_outcome(args: dict) -> dict:
        item_id = args.get("item_id", "")
        outcome = args.get("outcome", "")

        # 验证商品存在
        item = db_get_item(item_id)
        if not item:
            return {"success": False, "error": f"商品 {item_id} 不存在"}

        if outcome == "deal":
            deal_price = args.get("deal_price")
            if not deal_price or deal_price <= 0:
                return {"success": False, "error": "成交价无效，请提供正数的成交价"}
            return record_sim_deal(item_id, float(deal_price))

        elif outcome == "fail":
            buyer_offer = args.get("buyer_offer")
            seller_counter = args.get("seller_counter", 0)
            reason = args.get("reason", "buyer_declined")
            if not buyer_offer or buyer_offer <= 0:
                return {"success": False, "error": "请提供买家出价"}
            return record_sim_fail(item_id, float(buyer_offer),
                                   float(seller_counter) if seller_counter else 0, reason)

        else:
            return {"success": False, "error": "outcome 必须是 deal 或 fail"}

    # ── 2. get_bargain_insights ──
    def _handle_get_insights(args: dict) -> dict:
        item_id = args.get("item_id", "")
        stats = get_bargain_stats(item_id)
        if not stats:
            return {"success": False, "error": f"商品 {item_id} 暂无议价数据，请先进行模拟议价"}

        try:
            from .bargain_data import get_bargain_stats as _get_raw_stats
            raw = _get_raw_stats(item_id)

            # 用 PriceLearner 生成算法建议
            learner = learner_from_db_record(raw)
            suggestion = learner.suggest()
            accept_prob_90 = learner.acceptance_probability(
                raw["asking_price"] * 0.9)
            accept_prob_80 = learner.acceptance_probability(
                raw["asking_price"] * 0.8)

            return {
                "success": True,
                "item_id": item_id,
                "item_name": stats["item_name"],
                "basic_stats": stats,
                "ai_suggestion": suggestion,
                "algorithm_insight": (
                    f"[内部参考] {suggestion['data_points']['sim_deals']}笔成交/"
                    f"{suggestion['data_points']['sim_fails']}笔失败/"
                    f"{suggestion['data_points']['real_deals']}笔真实，"
                    f"建议起始{suggestion['starting_offer']:.0f}，"
                    f"预计{suggestion['expected_price']:.0f}，"
                    f"置信度{suggestion['confidence']:.0%}"
                )
            }
        except Exception as e:
            # 如果学习器出错，至少返回基础统计
            return {
                "success": True,
                "item_id": stats["item_id"],
                "basic_stats": stats,
                "ai_suggestion": None,
                "algorithm_insight": f"基础统计可用，但算法分析出现异常: {str(e)}"
            }

    # ── 3. report_real_transaction ──
    def _handle_report_real(args: dict) -> dict:
        item_id = args.get("item_id", "")
        final_price = args.get("final_price")
        if not final_price or final_price <= 0:
            return {"success": False, "error": "请提供有效的真实成交价"}

        item = db_get_item(item_id)
        if not item:
            return {"success": False, "error": f"商品 {item_id} 不存在"}

        result = record_real_deal(item_id, float(final_price))

        # 如果成功，顺便更新商品状态为 sold
        if result.get("success"):
            from .database import update_item_status
            update_item_status(item_id, "sold")

        return result

    handler_dict.update({
        "record_bargain_outcome": _handle_record_outcome,
        "get_bargain_insights": _handle_get_insights,
        "report_real_transaction": _handle_report_real,
    })


# ═══════════════════════════════════════════════════════════
# DeepSeek API 调用
# ═══════════════════════════════════════════════════════════

def call_deepseek(messages: List[Dict], tools: Optional[List[Dict]] = None,
                  stream: bool = False, temperature: float = 0.7,
                  max_tokens: int = 4096) -> Dict:
    if not DEEPSEEK_API_KEY:
        return _error_response("服务未配置 DEEPSEEK_API_KEY，请联系管理员")

    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        return _error_response("DeepSeek API 请求超时，请稍后重试")
    except requests.exceptions.HTTPError as e:
        body = resp.text[:500] if hasattr(resp, 'text') else str(e)
        return _error_response(f"DeepSeek API 返回错误 ({resp.status_code}): {body}")
    except requests.exceptions.RequestException as e:
        return _error_response(f"DeepSeek API 调用失败: {str(e)}")


def _error_response(msg: str) -> Dict:
    return {
        "error": msg,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": f"⚠️ {msg}"},
            "finish_reason": "stop"
        }]
    }


# ═══════════════════════════════════════════════════════════
# SSE 工具函数（流式）
# ═══════════════════════════════════════════════════════════

def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

def _sse_done() -> str:
    return "data: [DONE]\n\n"

def _sse_error(msg: str) -> str:
    return _sse_event({
        "error": {"message": msg, "type": "server_error"}
    })


# ═══════════════════════════════════════════════════════════
# 核心 Agent 循环
# ═══════════════════════════════════════════════════════════

def run_agent(messages: List[Dict], stream: bool = False,
              temperature: float = 0.7, max_tokens: int = 4096,
              conv_id: Optional[str] = None) -> Any:
    _register_handlers()
    system_prompt = load_system_prompt()

    global _current_conv_id
    _current_conv_id = conv_id

    full_messages = [{"role": "system", "content": system_prompt}]

    # 注入完整对话历史
    if conv_id:
        try:
            from .conversation_store import get_history, format_history_for_prompt
            history = get_history(conv_id)
            history_msgs = format_history_for_prompt(history)
            if history_msgs:
                full_messages.extend(history_msgs)
        except Exception:
            pass

    for msg in messages:
        if msg.get("role") == "system":
            continue
        full_messages.append(msg)

    if stream:
        return _stream_agent_loop(full_messages, temperature, max_tokens)
    else:
        content, finish_reason, usage = _agent_loop(full_messages, temperature, max_tokens)
        return _build_response(content, finish_reason, usage)


# ── 非流式 Agent 循环 ──────────────────────────────────

def _agent_loop(messages: List[Dict], temperature: float, max_tokens: int):
    tool_call_count = 0
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    while tool_call_count < MAX_TOOL_CALLS:
        response = call_deepseek(
            messages, tools=TOOL_DEFINITIONS,
            temperature=temperature, max_tokens=max_tokens
        )

        if "usage" in response:
            u = response["usage"]
            total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += u.get("completion_tokens", 0)
            total_usage["total_tokens"] += u.get("total_tokens", 0)

        if response.get("error"):
            return (str(response["error"]), "stop", total_usage)

        choice = response["choices"][0]
        message = choice["message"]

        if not message.get("tool_calls"):
            return (
                message.get("content") or "",
                choice.get("finish_reason", "stop"),
                total_usage,
            )

        tool_call_count += 1
        messages.append(message)

        for tool_call in message["tool_calls"]:
            tool_name = tool_call["function"]["name"]
            try:
                tool_args = json.loads(tool_call["function"]["arguments"])
            except json.JSONDecodeError:
                tool_args = {}

            handler = TOOL_HANDLERS.get(tool_name)
            if handler:
                try:
                    result = handler(tool_args)
                except Exception as e:
                    result = {"error": f"工具执行异常: {str(e)}"}
            else:
                result = {"error": f"未知工具: {tool_name}"}

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": json.dumps(result, ensure_ascii=False)
            })

    return ("处理超时，请重试或简化你的请求。", "stop", total_usage)


# ── 流式 Agent 循环 ────────────────────────────────────

def _stream_agent_loop(messages: List[Dict], temperature: float,
                       max_tokens: int) -> Generator[str, None, None]:
    tool_call_count = 0
    response_id = f"chatcmpl-{int(time.time())}"
    created = int(time.time())
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    while tool_call_count < MAX_TOOL_CALLS:
        response = call_deepseek(
            messages, tools=TOOL_DEFINITIONS,
            temperature=temperature, max_tokens=max_tokens
        )

        if "usage" in response:
            u = response["usage"]
            total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += u.get("completion_tokens", 0)
            total_usage["total_tokens"] += u.get("total_tokens", 0)

        if response.get("error"):
            yield _sse_event({
                "id": response_id, "object": "chat.completion.chunk",
                "created": created, "model": DEFAULT_MODEL,
                "choices": [{
                    "index": 0,
                    "delta": {"content": f"⚠️ {response['error']}"},
                    "finish_reason": "stop"
                }]
            })
            yield _sse_done()
            return

        choice = response["choices"][0]
        message = choice["message"]

        if not message.get("tool_calls"):
            content = message.get("content") or ""
            yield _sse_event({
                "id": response_id, "object": "chat.completion.chunk",
                "created": created, "model": DEFAULT_MODEL,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
            })
            if content:
                yield _sse_event({
                    "id": response_id, "object": "chat.completion.chunk",
                    "created": created, "model": DEFAULT_MODEL,
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
                })
            yield _sse_event({
                "id": response_id, "object": "chat.completion.chunk",
                "created": created, "model": DEFAULT_MODEL,
                "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason", "stop")}],
                "usage": total_usage,
            })
            yield _sse_done()
            return

        tool_call_count += 1
        messages.append(message)

        for tool_call in message["tool_calls"]:
            tool_name = tool_call["function"]["name"]
            try:
                tool_args = json.loads(tool_call["function"]["arguments"])
            except json.JSONDecodeError:
                tool_args = {}

            handler = TOOL_HANDLERS.get(tool_name)
            if handler:
                try:
                    result = handler(tool_args)
                except Exception as e:
                    result = {"error": f"工具执行异常: {str(e)}"}
            else:
                result = {"error": f"未知工具: {tool_name}"}

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": json.dumps(result, ensure_ascii=False)
            })

    yield _sse_event({
        "id": response_id, "object": "chat.completion.chunk",
        "created": created, "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "delta": {"content": "处理超时，请重试或简化请求。"}, "finish_reason": "stop"}],
        "usage": total_usage,
    })
    yield _sse_done()


# ═══════════════════════════════════════════════════════════
# 响应构建
# ═══════════════════════════════════════════════════════════

def _build_response(content: str, finish_reason: str = "stop",
                    usage: Optional[Dict] = None) -> Dict:
    if usage is None:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": DEFAULT_MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": usage,
    }
