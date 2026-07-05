"""
对话记忆管理（Conversation Store）

由于清小搭平台每次只发当前消息（不发完整历史），
后端自己维护对话上下文，确保多轮对话的连贯性。

存储策略：
- 内存存储（进程重启后丢失，MVP 阶段够用）
- 每 30 分钟自动清理过期对话
- 每个对话最多保留 30 条消息
- **IP 索引**：同一 IP 在 10 分钟内的请求自动归属同一对话
"""

import time
import threading
import uuid
from typing import List, Dict, Optional

# ── 内存存储 ──────────────────────────────────────────────

_store: Dict[str, dict] = {}
_ip_index: Dict[str, list] = {}  # ip -> [(timestamp, conv_id)]
_lock = threading.Lock()

MAX_CONVERSATIONS = 1000
MAX_MESSAGES = 30
TTL = 1800  # 30 分钟
IP_SESSION_TTL = 600  # 10 分钟：同一 IP 在 10 分钟内的请求归为同一对话


# ── 公开接口 ──────────────────────────────────────────────

def create_conversation() -> str:
    """创建一个新对话，返回 conversation_id"""
    conv_id = uuid.uuid4().hex[:12]
    with _lock:
        _store[conv_id] = {
            "messages": [],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    return conv_id


def get_history(conv_id: str) -> List[dict]:
    """获取指定对话的消息历史（返回副本）"""
    with _lock:
        conv = _store.get(conv_id)
        if conv:
            conv["updated_at"] = time.time()
            return list(conv["messages"])
        return []


def append_user_message(conv_id: str, content):
    """追加用户消息到历史"""
    with _lock:
        conv = _store.get(conv_id)
        if not conv:
            return
        if isinstance(content, str):
            conv["messages"].append({
                "role": "user", "content": content,
                "timestamp": time.time()
            })
        elif isinstance(content, list):
            # 多模态消息
            text_parts = []
            for part in content:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    text_parts.append("[图片]")
            conv["messages"].append({
                "role": "user",
                "content": " ".join(text_parts),
                "timestamp": time.time()
            })
        _trim_and_cleanup(conv)


def append_assistant_message(conv_id: str, content: str):
    """追加助手回复到历史"""
    with _lock:
        conv = _store.get(conv_id)
        if not conv:
            return
        conv["messages"].append({
            "role": "assistant", "content": content,
            "timestamp": time.time()
        })
        _trim_and_cleanup(conv)


def get_or_create(conv_id: Optional[str]) -> str:
    """
    获取或创建对话。
    如果 conv_id 存在且有效，返回它；否则创建新对话。
    """
    if conv_id:
        with _lock:
            if conv_id in _store:
                _store[conv_id]["updated_at"] = time.time()
                return conv_id
    return create_conversation()


# ── IP 索引（无需会话 ID 也能找回同一对话） ──────────────

def find_by_ip(client_ip: str) -> Optional[str]:
    """
    通过客户端 IP 查找最近活跃的对话。
    同一 IP 在 IP_SESSION_TTL 秒内的请求，自动归属到同一对话。
    """
    now = time.time()
    with _lock:
        records = _ip_index.get(client_ip, [])
        # 只保留有效记录
        valid = [(ts, cid) for ts, cid in records if (now - ts) < IP_SESSION_TTL]
        _ip_index[client_ip] = valid
        if valid:
            # 返回最近的那个
            return valid[-1][1]
        return None


def record_ip(client_ip: str, conv_id: str):
    """记录 IP 到 conv_id 的映射"""
    now = time.time()
    with _lock:
        if client_ip not in _ip_index:
            _ip_index[client_ip] = []
        _ip_index[client_ip].append((now, conv_id))
        # 只保留最近 50 条记录
        if len(_ip_index[client_ip]) > 50:
            _ip_index[client_ip] = _ip_index[client_ip][-50:]


def format_history_for_prompt(history: List[dict], max_rounds: int = 10) -> List[dict]:
    """
    将历史消息格式化为 OpenAI 消息格式。
    只保留最近 max_rounds 轮对话，去掉 timestamp 字段。
    """
    recent = history[-(max_rounds * 2):]  # 保留最近 N 轮
    formatted = []
    for msg in recent:
        formatted.append({
            "role": msg["role"],
            "content": msg["content"],
        })
    return formatted


# ── 内部方法 ──────────────────────────────────────────────

def _trim_and_cleanup(conv: dict):
    """修剪消息数量 + 清理过期对话"""
    if len(conv["messages"]) > MAX_MESSAGES:
        conv["messages"] = conv["messages"][-MAX_MESSAGES:]

    # 定期清理（每 10 次操作做一次）
    if len(_store) > MAX_CONVERSATIONS:
        now = time.time()
        expired = [
            cid for cid, c in _store.items()
            if now - c["updated_at"] > TTL
        ]
        for cid in expired:
            del _store[cid]
