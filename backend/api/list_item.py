"""
商品发布模块

功能：
- 接收智能体发来的结构化商品信息
- 写入 SQLite 数据库（替代原来的 JSON 文件）
- 为每件商品生成唯一 ID

后续可无缝迁移到 PostgreSQL / MySQL
"""

import json
import os
import uuid
from datetime import datetime
from .database import add_item, update_item_status, get_item, record_tags


# ═══════════════════════════════════════════════════════════
# 核心操作
# ═══════════════════════════════════════════════════════════

def generate_item_id():
    """生成唯一商品编号：ITEM-YYYYMMDD-XXXX"""
    date_part = datetime.now().strftime("%Y%m%d")
    short_id = str(uuid.uuid4()).split("-")[0][:4].upper()
    return f"ITEM-{date_part}-{short_id}"


def _validate_tags(tags: list) -> list:
    """
    校验标签：
    - 最多 5 个
    - 每个标签 1-10 个汉字/字符
    - 去重、去空、去前后空格
    """
    if not tags:
        return []
    cleaned = []
    for tag in tags:
        t = str(tag).strip()
        if t and 1 <= len(t) <= 20 and t not in cleaned:
            cleaned.append(t)
    return cleaned[:5]


def handle_list_item(data: dict) -> dict:
    """
    处理商品发布请求。

    输入数据格式：
    {
        "name": "二手自行车",              # 必填
        "description": "九成新...",        # 必填
        "contact_type": "wechat",          # 必填: wechat / phone / email / in_person
        "contact_value": "thuxxx_2024",    # 必填
        "price": "300",                    # 必填
        "tags": ["通勤", "学生"],          # 必填（至少1个）
        "image_url": "https://...",        # 可选
        "image_description": "...",        # 可选
        "category": "交通工具",            # 可选
    }

    输出格式：
    {
        "success": true,
        "item_id": "ITEM-20260703-0001",
        "message": "发布成功"
    }
    """
    # ── 校验必填字段 ──
    name = data.get("name", "").strip()
    if not name:
        return {"success": False, "error": "物品名称不能为空"}

    description = data.get("description", "").strip()
    if not description:
        return {"success": False, "error": "物品描述不能为空"}

    contact_type = data.get("contact_type", "").strip()
    if contact_type not in ("wechat", "phone", "email", "in_person"):
        return {"success": False, "error": "联系方式类型无效，请选择 wechat/phone/email/in_person"}

    contact_value = data.get("contact_value", "").strip()
    if not contact_value:
        return {"success": False, "error": "联系方式不能为空"}

    price = data.get("price", "").strip()
    if not price:
        return {"success": False, "error": "请填写价格或价格范围，如'300'或'200-350'"}

    tags = _validate_tags(data.get("tags", []))
    if not tags:
        return {"success": False, "error": "请至少提供一个标签，如：文具、生活用品、书、电子、交通、运动、服装等"}

    # ── 构建商品对象 ──
    item_id = generate_item_id()
    now = datetime.now().isoformat()

    item = {
        "item_id": item_id,
        "name": name,
        "description": description,
        "contact_type": contact_type,
        "contact_value": contact_value,
        "contact_label": {
            "wechat": "微信",
            "phone": "手机号",
            "email": "邮箱",
            "in_person": "当面交易",
        }.get(contact_type, contact_type),
        "image_url": "",
        "image_description": "",
        "category": data.get("category", ""),
        "price": price,
        "tags": tags,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }

    # ── 写入数据库 ──
    ok = add_item(item)
    if not ok:
        return {"success": False, "error": "数据库写入失败，请重试"}

    # ── 更新标签频率统计 ──
    if tags:
        record_tags(tags)

    # 组装发布成功回复
    tags_str = "、".join([f"#{t}" for t in tags]) if tags else "（未设置标签）"
    return {
        "success": True,
        "item_id": item_id,
        "item_name": name,
        "tags": tags,
        "tags_display": tags_str,
        "message": (
            f"🎉 发布成功！以下是你发布的物品信息：\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 物品：{name}\n"
            f"🏷️ 标签：{tags_str}\n"
            f"🆔 编号：{item_id}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💡 交易小贴士：\n"
            f"· 建议线下当面交易，选择校内公共场所（如C楼、紫荆操场）\n"
            f"· 确认物品状况后再付款\n"
            f"· 交易时注意保护个人安全\n\n"
            f"你可以随时告诉我要「下架」或「卖掉了」来更新状态。\n"
            f"同时欢迎逛逛其他同学发布的二手好物～"
        ),
    }


# ═══════════════════════════════════════════════════════════
# 查询函数（供其他模块调用）
# ═══════════════════════════════════════════════════════════

def get_item_by_id(item_id: str) -> dict:
    """根据 ID 获取单件商品"""
    return get_item(item_id)


def get_active_items() -> list:
    """获取所有在售商品列表"""
    from .database import get_active_items as _db_get_active
    return _db_get_active()
