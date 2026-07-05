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
                        "description": "物品分类（可选），如'交通工具'、'电子产品'、'教材'"
                    }
                },
                "required": ["name", "description", "price", "contact_type", "contact_value", "tags"]
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
            "description": "更新商品状态：active(在售) / sold(已售出) / deleted(已下架)。用户说'卖掉了'或'下架'时调用。需要知道商品编号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "商品ID"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "sold", "deleted"],
                        "description": "目标状态：active(在售) / sold(已售) / deleted(下架)"
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
    }
]


# ═══════════════════════════════════════════════════════════
# 工具执行器
# ═══════════════════════════════════════════════════════════

TOOL_HANDLERS = {}
_current_conv_id: Optional[str] = None


def _register_handlers():
    """延迟导入工具处理器（避免循环依赖）"""
    from .list_item import handle_list_item
    from .search_item import handle_search_item
    from .web_search import handle_web_search
    from .tag_utils import get_popular_tags as _get_popular_tags, search_by_tag as _search_by_tag
    from .database import update_item_status as _db_update_status, get_item as _db_get_item

    def _handle_status(args: dict) -> dict:
        """更新商品状态"""
        item_id = args.get("item_id", "")
        status = args.get("status", "")
        if status not in ("active", "sold", "deleted"):
            return {"success": False, "error": "无效的状态值，请用 active/sold/deleted"}
        item = _db_get_item(item_id)
        if not item:
            return {"success": False, "error": "商品不存在"}
        ok = _db_update_status(item_id, status)
        if not ok:
            return {"success": False, "error": "更新失败"}
        return {"success": True, "status": status, "message": f"商品状态已更新为 {status}"}

    TOOL_HANDLERS.update({
        "list_item": lambda args: handle_list_item(args),
        "search_item": lambda args: handle_search_item(args.get("query", "")),
        "web_search": lambda args: handle_web_search(args.get("query", "")),
        "update_status": lambda args: _handle_status(args),
        "get_popular_tags": lambda args: {"success": True, "tags": _get_popular_tags(3)},
        "search_by_tag": lambda args: _handle_search_by_tag(args, _search_by_tag),
    })


def _handle_search_by_tag(args: dict, search_by_tag) -> dict:
    tag = args.get("tag", "").strip()
    if not tag:
        return {"success": False, "error": "请输入标签"}
    items = search_by_tag(tag)
    return {"success": True, "tag": tag, "total": len(items), "items": items}


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
