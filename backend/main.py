"""
THU 校园二手集市智能体 — 后端统一入口

架构（2026-07-03 更新）：
  清小搭(代理) → /v1/chat/completions (OpenAI 格式) → 后端(LLM + 工具)

同时保留原有的 8 个工具 API 端点，供调试和直接调用。

快速启动：
    python main.py

部署到公网后，在清小搭平台填入：
    API 地址：https://你的域名/v1
    API 密钥：AGENT_API_KEY（见 .env）
    模型名称：deepseek-chat
"""

import os
import json
import uuid
import functools
import base64
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ============================================================
# 各模块导入
# ============================================================
from api.list_item import handle_list_item
from api.search_item import handle_search_item
from api.web_search import handle_web_search
from api.embedding import handle_embed
from api.tag_utils import get_popular_tags, search_by_tag
from api.agent import run_agent
from api.conversation_store import (
    get_or_create, get_history, append_user_message, append_assistant_message,
    find_by_ip, record_ip,
)


# ============================================================
# API Key 认证（用于 /v1/* 端点）
# ============================================================

AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")


def require_api_key(f):
    """装饰器：检查 Authorization: Bearer <key>"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if AGENT_API_KEY:
            # 需要认证
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": {"message": "缺少 API Key", "type": "auth_error"}}), 401
            token = auth_header[7:]
            if token != AGENT_API_KEY:
                return jsonify({"error": {"message": "API Key 无效", "type": "auth_error"}}), 401
        # 没设置 AGENT_API_KEY 时放行（开发环境）
        return f(*args, **kwargs)
    return decorated


# ============================================================
# 清小搭标准协议端点 — OpenAI 兼容
# ============================================================

@app.route("/v1/models", methods=["GET"])
@require_api_key
def list_models():
    """
    GET /v1/models — 连通性与凭证校验端点。
    清小搭探测时先请求此端点验证凭证和连通性。
    """
    return jsonify({
        "object": "list",
        "data": [
            {
                "id": "deepseek-chat",
                "object": "model",
                "owned_by": "deepseek",
            }
        ]
    })

@app.route("/v1/chat/completions", methods=["POST"])
@require_api_key
def chat_completions():
    """
    OpenAI 兼容的 Chat Completions 端点。
    清小搭「标准协议接入」的核心端点。

    请求体格式（OpenAI 标准）：
    {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "我想卖自行车"}
        ],
        "stream": false,
        "temperature": 0.7,
        "max_tokens": 4096
    }

    响应格式（OpenAI 标准）：
    {
        "id": "chatcmpl-...",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "deepseek-chat",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "你好！你想出售什么物品？"},
            "finish_reason": "stop"
        }]
    }
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({
            "error": {"message": "请求体必须是有效的 JSON", "type": "invalid_request_error"}
        }), 400

    # 提取参数
    messages = data.get("messages", [])
    stream = data.get("stream", False)
    temperature = data.get("temperature", 0.7)
    max_tokens = data.get("max_tokens", 4096)

    if not messages:
        return jsonify({
            "error": {"message": "messages 不能为空", "type": "invalid_request_error"}
        }), 400

    # ===== 对话记忆管理 =====
    # 清小搭每次只发当前消息，后端自己维护上下文
    # 策略：优先用请求中的 conv_id，否则用 IP 查找最近对话，否则新建
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    conv_id = data.get("conversation_id") or find_by_ip(client_ip)
    conv_id = get_or_create(conv_id)
    record_ip(client_ip, conv_id)

    # 暂存当前用户消息，等助手回复后一并存入历史（避免重复）
    current_user_content = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            current_user_content = msg.get("content", "")
            break

    if stream:
        # 流式响应
        def generate():
            collected_content = []
            for event in run_agent(messages, stream=True, temperature=temperature,
                                   max_tokens=max_tokens, conv_id=conv_id):
                if event.startswith("data: ") and "[DONE]" not in event:
                    try:
                        chunk = json.loads(event[6:])
                        for c in chunk.get("choices", []):
                            if c.get("delta", {}).get("content"):
                                collected_content.append(c["delta"]["content"])
                    except json.JSONDecodeError:
                        pass
                yield event
            full_response = "".join(collected_content)
            if full_response and current_user_content:
                append_user_message(conv_id, current_user_content)
                append_assistant_message(conv_id, full_response)

        resp = Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
        resp.headers["X-Conversation-Id"] = conv_id
        return resp

    else:
        # 非流式响应
        try:
            result = run_agent(messages, stream=False, temperature=temperature,
                               max_tokens=max_tokens, conv_id=conv_id)
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content and current_user_content:
                append_user_message(conv_id, current_user_content)
                append_assistant_message(conv_id, content)
            result["conversation_id"] = conv_id
            return jsonify(result)
        except Exception as e:
            return jsonify(_error_response(f"服务内部错误: {str(e)}")), 500


# ============================================================
# 原有工具 API（保持不变，供调试和直接调用）
# ============================================================

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "service": "thu-secondhand-agent"})


@app.route("/chat")
def chat_page():
    """提供测试页面（同源，避免图片跨域问题）"""
    return send_from_directory(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "chat.html"
    )


@app.route("/api/list_item", methods=["POST"])
def list_item():
    """发布商品"""
    data = request.get_json(force=True)
    result = handle_list_item(data)
    return jsonify(result)


@app.route("/api/search_item", methods=["POST"])
def search_item():
    """搜索商品（语义匹配）"""
    data = request.get_json(force=True)
    query = data.get("query", "")
    result = handle_search_item(query)
    return jsonify(result)


@app.route("/api/analyze_image", methods=["POST"])
def analyze_image():
    """分析图片内容"""
    data = request.get_json(force=True)
    image_url = data.get("image_url", "")
    result = handle_analyze_image(image_url)
    return jsonify(result)


@app.route("/api/web_search", methods=["POST"])
def web_search():
    """搜索网络信息"""
    data = request.get_json(force=True)
    query = data.get("query", "")
    result = handle_web_search(query)
    return jsonify(result)


@app.route("/api/embed", methods=["POST"])
def embed():
    """生成文本向量"""
    data = request.get_json(force=True)
    texts = data.get("texts", [])
    result = handle_embed(texts)
    return jsonify(result)


@app.route("/api/get_item_detail", methods=["POST"])
def get_item_detail():
    """获取商品详情"""
    from api.database import get_item as _db_get
    data = request.get_json(force=True)
    item_id = data.get("item_id", "")
    item = _db_get(item_id)
    if not item:
        return jsonify({"error": "商品不存在或已下架"}), 404
    return jsonify({"item": item})


@app.route("/api/update_status", methods=["POST"])
def update_status():
    """更新商品状态"""
    from api.database import update_item_status as _db_update, get_item as _db_get
    data = request.get_json(force=True)
    item_id = data.get("item_id", "")
    status = data.get("status", "")
    if status not in ("active", "sold", "deleted"):
        return jsonify({"error": "无效的状态值"}), 400
    if not _db_get(item_id):
        return jsonify({"error": "商品不存在"}), 404
    ok = _db_update(item_id, status)
    if not ok:
        return jsonify({"error": "更新失败"}), 500
    return jsonify({"success": True, "status": status})


@app.route("/api/get_popular_tags", methods=["GET"])
def popular_tags():
    """获取热门标签推荐（Top3）"""
    top = request.args.get("top", 3, type=int)
    tags = get_popular_tags(top)
    return jsonify({"success": True, "tags": tags})


@app.route("/api/search_by_tag", methods=["POST"])
def search_by_tag_endpoint():
    """按标签搜索商品"""
    data = request.get_json(force=True)
    tag = data.get("tag", "").strip()
    if not tag:
        return jsonify({"success": False, "error": "请输入标签"})
    items = search_by_tag(tag)
    return jsonify({
        "success": True,
        "tag": tag,
        "total": len(items),
        "items": items,
    })



# ============================================================
# 辅助函数
# ============================================================

def _error_response(msg: str) -> dict:
    return {
        "error": {"message": msg, "type": "server_error"},
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": f"⚠️ {msg}"},
            "finish_reason": "stop",
        }]
    }


# ============================================================
# 启动
# ============================================================

# 启动时初始化数据库
from api.database import init_db
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 清小闲后端启动中...")
    print(f"   OpenAI 兼容端点: http://0.0.0.0:{port}/v1/chat/completions")
    print(f"   工具 API:         http://0.0.0.0:{port}/api/...")
    print(f"   健康检查:         http://0.0.0.0:{port}/")
    app.run(host="0.0.0.0", port=port, debug=True)
