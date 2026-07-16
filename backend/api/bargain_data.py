"""
议价数据模块 — 模拟讨价还价的数据持久化

数据表结构（SQLite）：
  bargain_data:
    item_id          TEXT PK          — 商品编号
    item_name        TEXT             — 商品名称
    asking_price     REAL             — 标价
    seller_min_price REAL             — 卖家底价
    sim_count        INTEGER          — 模拟议价总次数
    sim_deal_count   INTEGER          — 模拟成交次数
    sim_fail_count   INTEGER          — 模拟失败次数
    sim_prices       TEXT(JSON)       — 模拟成交价列表 [250, 245, ...]
    sim_fails        TEXT(JSON)       — 模拟失败记录 [{"buyer_offer":220, "seller_counter":260}, ...]
    real_count       INTEGER          — 真实成交次数
    real_prices      TEXT(JSON)       — 真实成交价列表 [255, ...]

核心能力：
  1. 记录模拟议价结果（成交/未成交）
  2. 记录真实交易价格
  3. 查询某个商品的所有议价统计数据
  4. 实时计算平均成交价（支持多个用户同时模拟）
"""

import sqlite3
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "items.db")  # 复用在 main.py 中 init_db 创建的同一数据库


# ═══════════════════════════════════════════════════════════
# 初始化表
# ═══════════════════════════════════════════════════════════

def init_bargain_table():
    """创建议价数据表（幂等）"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bargain_data (
            item_id          TEXT PRIMARY KEY,
            item_name        TEXT NOT NULL,
            category         TEXT DEFAULT '',
            asking_price     REAL NOT NULL,
            seller_min_price REAL NOT NULL,
            sim_count        INTEGER DEFAULT 0,
            sim_deal_count   INTEGER DEFAULT 0,
            sim_fail_count   INTEGER DEFAULT 0,
            sim_prices       TEXT DEFAULT '[]',
            sim_fails        TEXT DEFAULT '[]',
            real_count       INTEGER DEFAULT 0,
            real_prices      TEXT DEFAULT '[]',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        );
    """)
    # 兼容旧表：如果 category 列不存在则添加
    try:
        conn.execute("ALTER TABLE bargain_data ADD COLUMN category TEXT DEFAULT ''")
    except Exception:
        pass  # 列已存在，忽略
    # 从 items 表回填分类（仅对 category 为空的历史数据）
    conn.execute("""
        UPDATE bargain_data SET category = (
            SELECT category FROM items WHERE items.item_id = bargain_data.item_id
        ) WHERE category = '' AND EXISTS (
            SELECT 1 FROM items WHERE items.item_id = bargain_data.item_id AND items.category != ''
        )
    """)
    conn.commit()
    conn.close()


def _get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("sim_prices", "sim_fails", "real_prices"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = [] if field != "sim_fails" else []
    return d


# ═══════════════════════════════════════════════════════════
# 初始化/更新商品议价记录
# ═══════════════════════════════════════════════════════════

def init_item_bargain(item_id: str, item_name: str,
                      asking_price: float, seller_min_price: float,
                      category: str = "") -> bool:
    """
    为新上架商品创建议价记录（如果已存在则忽略）。
    需要在 list_item 流程中，卖家提供了底价后调用。
    """
    conn = _get_conn()
    now = datetime.now().isoformat()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO bargain_data
                (item_id, item_name, category, asking_price, seller_min_price,
                 sim_count, sim_deal_count, sim_fail_count,
                 sim_prices, sim_fails, real_count, real_prices,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, 0, 0, '[]', '[]', 0, '[]', ?, ?)
        """, (item_id, item_name, category, asking_price, seller_min_price, now, now))
        conn.commit()
        return True
    except Exception as e:
        print(f"[bargain_data] init_item_bargain error: {e}")
        return False
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════
# 记录模拟成交
# ═══════════════════════════════════════════════════════════

def record_sim_deal(item_id: str, deal_price: float) -> dict:
    """
    记录一次模拟议价成交。
    返回更新后的统计摘要，供 AI 展示给用户。
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM bargain_data WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not row:
            return {"error": "商品不存在"}
        d = _row_to_dict(row)

        # 更新数据
        sim_prices = d.get("sim_prices", [])
        sim_prices.append(deal_price)
        avg_price = sum(sim_prices) / len(sim_prices)

        conn.execute("""
            UPDATE bargain_data
            SET sim_count = sim_count + 1,
                sim_deal_count = sim_deal_count + 1,
                sim_prices = ?,
                updated_at = ?
            WHERE item_id = ?
        """, (json.dumps(sim_prices, ensure_ascii=False),
              datetime.now().isoformat(), item_id))
        conn.commit()

        return {
            "success": True,
            "item_id": item_id,
            "item_name": d["item_name"],
            "deal_price": deal_price,
            "avg_price": round(avg_price, 2),
            "total_sim_deals": len(sim_prices),
            "message": f"模拟成交！成交价 {deal_price} 元。当前该商品共模拟成交 {len(sim_prices)} 次，平均模拟成交价 {avg_price:.0f} 元。"
        }
    except Exception as e:
        return {"error": f"记录失败: {e}"}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════
# 记录模拟未成交
# ═══════════════════════════════════════════════════════════

def record_sim_fail(item_id: str, buyer_last_offer: float,
                    seller_last_counter: float, reason: str = "buyer_declined") -> dict:
    """
    记录一次模拟议价失败。
    reason: buyer_declined(买家放弃) / seller_rejected(卖家拒绝) / timeout(超时)
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM bargain_data WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not row:
            return {"error": "商品不存在"}
        d = _row_to_dict(row)

        sim_fails = d.get("sim_fails", [])
        sim_fails.append({
            "buyer_offer": buyer_last_offer,
            "seller_counter": seller_last_counter,
            "reason": reason
        })

        conn.execute("""
            UPDATE bargain_data
            SET sim_count = sim_count + 1,
                sim_fail_count = sim_fail_count + 1,
                sim_fails = ?,
                updated_at = ?
            WHERE item_id = ?
        """, (json.dumps(sim_fails, ensure_ascii=False),
              datetime.now().isoformat(), item_id))
        conn.commit()

        # 分析：根据失败记录推断价格下限
        all_offers = [f["buyer_offer"] for f in sim_fails]
        implied_floor = max(all_offers) if all_offers else None

        info = f"未成交记录已保存。该商品累计模拟失败 {len(sim_fails)} 次。"
        if implied_floor:
            info += f" 根据失败记录推断卖家可能不接受低于 {implied_floor:.0f} 元的价格。"

        return {
            "success": True,
            "item_id": item_id,
            "item_name": d["item_name"],
            "total_fails": len(sim_fails),
            "implied_floor": implied_floor,
            "message": info
        }
    except Exception as e:
        return {"error": f"记录失败: {e}"}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════
# 记录真实成交价
# ═══════════════════════════════════════════════════════════

def record_real_deal(item_id: str, final_price: float) -> dict:
    """
    用户在线下完成真实交易后，回来报告最终成交价。
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM bargain_data WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not row:
            return {"error": "商品不存在"}
        d = _row_to_dict(row)

        real_prices = d.get("real_prices", [])
        real_prices.append(final_price)
        avg_real = sum(real_prices) / len(real_prices)

        # 同时计算模拟 vs 真实的差距
        sim_prices = d.get("sim_prices", [])
        sim_avg = sum(sim_prices) / len(sim_prices) if sim_prices else None

        conn.execute("""
            UPDATE bargain_data
            SET real_count = real_count + 1,
                real_prices = ?,
                updated_at = ?
            WHERE item_id = ?
        """, (json.dumps(real_prices, ensure_ascii=False),
              datetime.now().isoformat(), item_id))
        conn.commit()

        result = {
            "success": True,
            "item_id": item_id,
            "item_name": d["item_name"],
            "final_price": final_price,
            "avg_real_price": round(avg_real, 2),
            "total_real_deals": len(real_prices),
        }

        # 如果有模拟数据，给出对比
        if sim_avg is not None:
            diff = final_price - sim_avg
            result["sim_avg_price"] = round(sim_avg, 2)
            result["diff_from_sim_avg"] = round(diff, 2)
            direction = "高于" if diff > 0 else "低于"
            result["message"] = (
                f"真实成交价 {final_price} 元已记录！"
                f"模拟平均价 {sim_avg:.0f} 元，真实价 {direction} 模拟均价 {abs(diff):.0f} 元。"
                f"这些数据将帮助系统优化议价建议。"
            )
        else:
            result["message"] = f"真实成交价 {final_price} 元已记录！感谢反馈 🙏"

        return result
    except Exception as e:
        return {"error": f"记录失败: {e}"}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════
# 查询议价统计数据
# ═══════════════════════════════════════════════════════════

def get_bargain_stats(item_id: str) -> Optional[dict]:
    """
    获取某个商品的所有议价统计数据。
    供 AI 在模拟议价时展示"当前行情"。
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM bargain_data WHERE item_id = ?", (item_id,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    d = _row_to_dict(row)

    sim_prices = d.get("sim_prices", [])
    sim_fails = d.get("sim_fails", [])
    real_prices = d.get("real_prices", [])
    all_deals = sim_prices + real_prices

    stats = {
        "item_id": d["item_id"],
        "item_name": d["item_name"],
        "asking_price": d["asking_price"],
        "seller_min_price": d["seller_min_price"],
        "sim_count": d["sim_count"],
        "sim_deal_count": d["sim_deal_count"],
        "sim_fail_count": d["sim_fail_count"],
        "sim_avg_price": round(sum(sim_prices) / len(sim_prices), 2) if sim_prices else None,
        "real_count": d["real_count"],
        "real_avg_price": round(sum(real_prices) / len(real_prices), 2) if real_prices else None,
        "implied_floor": max(f["buyer_offer"] for f in sim_fails) if sim_fails else None,
    }

    # 综合建议区间
    if all_deals:
        stats["suggested_range"] = {
            "low": min(all_deals),
            "high": max(all_deals),
            "avg": round(sum(all_deals) / len(all_deals), 2),
        }
    else:
        # 没有数据时用标价和底价
        stats["suggested_range"] = {
            "low": d["seller_min_price"],
            "high": d["asking_price"],
            "avg": round((d["asking_price"] + d["seller_min_price"]) / 2, 2),
        }

    return stats


# ═══════════════════════════════════════════════════════════
# 获取所有有议价数据的商品（供分析/算法使用）
# ═══════════════════════════════════════════════════════════

def get_all_bargain_items() -> List[dict]:
    """获取所有有议价记录的商品"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bargain_data ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_items_by_category(category: str) -> List[dict]:
    """获取同一分类的所有议价记录（用于跨商品学习）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bargain_data WHERE category = ? ORDER BY updated_at DESC",
        (category,)
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_all_categories() -> List[str]:
    """获取所有有数据的分类"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT category FROM bargain_data WHERE category != ''"
    ).fetchall()
    conn.close()
    return [r["category"] for r in rows]


# ═══════════════════════════════════════════════════════════
# 获取全局统计（Poster/R&D 用）
# ═══════════════════════════════════════════════════════════

def get_global_stats() -> dict:
    """
    获取全平台议价统计，用于学术分析和 Poster 展示。
    """
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM bargain_data").fetchall()
    conn.close()

    total_items = len(rows)
    total_sim = sum(r["sim_count"] for r in rows)
    total_sim_deals = sum(r["sim_deal_count"] for r in rows)
    total_sim_fails = sum(r["sim_fail_count"] for r in rows)
    total_real = sum(r["real_count"] for r in rows)

    # 收集所有价格
    all_sim_prices = []
    all_real_prices = []
    for r in rows:
        d = _row_to_dict(r)
        all_sim_prices.extend(d.get("sim_prices", []))
        all_real_prices.extend(d.get("real_prices", []))

    stats = {
        "total_items_with_bargain_data": total_items,
        "total_simulations": total_sim,
        "total_sim_deals": total_sim_deals,
        "total_sim_fails": total_sim_fails,
        "sim_deal_rate": round(total_sim_deals / total_sim * 100, 1) if total_sim > 0 else 0,
        "total_real_transactions_reported": total_real,
        "avg_sim_price": round(sum(all_sim_prices) / len(all_sim_prices), 2) if all_sim_prices else None,
        "avg_real_price": round(sum(all_real_prices) / len(all_real_prices), 2) if all_real_prices else None,
    }

    if all_sim_prices and all_real_prices:
        sim_avg = sum(all_sim_prices) / len(all_sim_prices)
        real_avg = sum(all_real_prices) / len(all_real_prices)
        stats["sim_vs_real_diff"] = round(real_avg - sim_avg, 2)
        stats["sim_vs_real_diff_pct"] = round((real_avg - sim_avg) / sim_avg * 100, 1)

    return stats
