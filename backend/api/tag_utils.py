"""
标签工具模块

功能：
- 跟踪标签使用频率（已迁移到 SQLite）
- 获取最热门的 N 个标签
- 按标签搜索商品

所有数据操作委托给 database.py，保持接口兼容。
"""

from .database import record_tags, get_popular_tags, search_items_by_tag


# ── 兼容接口 ────────────────────────────────────────────

def record_tags(tags: list):
    """记录一批标签的使用"""
    from .database import record_tags as _r
    return _r(tags)


def get_popular_tags(top_n: int = 3) -> list:
    """获取热门标签 TopN"""
    from .database import get_popular_tags as _g
    return _g(top_n)


def search_by_tag(tag: str) -> list:
    """按标签搜索商品"""
    from .database import search_items_by_tag as _s
    return _s(tag)
