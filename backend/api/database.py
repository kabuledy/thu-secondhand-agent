"""
SQLite 数据库模块

替代原来的 JSON 文件存储（items_db.json + tag_stats.json）。
优势：并发安全、查询快、不会因数据量大而崩。
Python 自带 sqlite3，无需额外安装依赖。
"""

import sqlite3
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "items.db")


# ═══════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════

def get_connection() -> sqlite3.Connection:
    """获取数据库连接（每次调用创建新连接，线程安全）"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")          # 读写并发优化
    conn.execute("PRAGMA busy_timeout=5000")         # 忙时等待5秒
    return conn


def init_db():
    """初始化数据库表结构（幂等，可重复调用）"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            item_id          TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            description      TEXT NOT NULL DEFAULT '',
            contact_type     TEXT NOT NULL,
            contact_value    TEXT NOT NULL,
            contact_label    TEXT DEFAULT '',
            image_url        TEXT DEFAULT '',
            image_description TEXT DEFAULT '',
            category         TEXT DEFAULT '',
            price            TEXT NOT NULL DEFAULT '',
            tags             TEXT DEFAULT '[]',
            status           TEXT DEFAULT 'active',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tag_stats (
            tag   TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
        CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);
    """)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════

def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """将 SQLite 行转为字典，自动解析 tags JSON"""
    d = dict(row)
    if isinstance(d.get("tags"), str):
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d


# ═══════════════════════════════════════════════════════════
# 商品 CRUD
# ═══════════════════════════════════════════════════════════

def add_item(item: dict) -> bool:
    """添加一件商品"""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO items (item_id, name, description, contact_type, contact_value,
                               contact_label, image_url, image_description, category,
                               price, tags, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["item_id"],
            item["name"],
            item.get("description", ""),
            item["contact_type"],
            item["contact_value"],
            item.get("contact_label", ""),
            item.get("image_url", ""),
            item.get("image_description", ""),
            item.get("category", ""),
            item["price"],
            json.dumps(item.get("tags", []), ensure_ascii=False),
            item.get("status", "active"),
            item["created_at"],
            item["updated_at"],
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"[database] add_item error: {e}")
        return False
    finally:
        conn.close()


def get_item(item_id: str) -> Optional[dict]:
    """根据 ID 获取单件商品"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def get_active_items() -> List[dict]:
    """获取所有在售商品，按发布时间降序"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM items WHERE status = 'active' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_item_status(item_id: str, new_status: str) -> bool:
    """更新商品状态（active / sold / deleted）"""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE items SET status = ?, updated_at = ? WHERE item_id = ?",
        (new_status, now, item_id),
    )
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def search_items_by_tag(tag: str) -> List[dict]:
    """
    按标签搜索在售商品。
    支持模糊匹配：搜索"书"能匹配"教材"、"小说"标签的物品。
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM items WHERE status = 'active' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    tag_lower = tag.strip().lower()
    if not tag_lower:
        return []

    matched = []
    for row in rows:
        item = _row_to_dict(row)
        item_tags = [t.lower() for t in item.get("tags", [])]
        if any(tag_lower == t or tag_lower in t or t in tag_lower for t in item_tags):
            # 图片由前端独立处理，不在名称中嵌入
            matched.append(item)
    return matched


def clear_all_items():
    """清空所有商品数据（测试用）"""
    conn = get_connection()
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM tag_stats")
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
# 标签统计
# ═══════════════════════════════════════════════════════════

def record_tags(tags: List[str]):
    """记录一批标签的使用次数"""
    if not tags:
        return
    conn = get_connection()
    for tag in tags:
        tag = tag.strip()
        if tag:
            conn.execute("""
                INSERT INTO tag_stats (tag, count) VALUES (?, 1)
                ON CONFLICT(tag) DO UPDATE SET count = count + 1
            """, (tag,))
    conn.commit()
    conn.close()


def get_popular_tags(top_n: int = 3) -> List[dict]:
    """
    获取使用频率最高的 N 个标签（按 count 降序）。
    如果标签总数不足 N 个，有多少返回多少。
    如果没有任何标签，返回空列表。
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT tag, count FROM tag_stats ORDER BY count DESC LIMIT ?",
        (top_n,),
    ).fetchall()
    conn.close()

    return [{"tag": r["tag"], "count": r["count"]} for r in rows]
