"""
查询商品和议价数据 — 只读操作，安全无副作用
"""
import sqlite3, json

conn = sqlite3.connect("backend/data/items.db")
conn.row_factory = sqlite3.Row

print("=" * 60)
print("  商品列表")
print("=" * 60)
rows = conn.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
if not rows:
    print("暂无商品")
for r in rows:
    d = dict(r)
    print(f"编号: {d['item_id']}")
    print(f"名称: {d['name']}")
    print(f"价格: ¥{d['price']}")
    print(f"描述: {d['description']}")
    print(f"联系方式: {d['contact_label']} | {d['contact_value'][:3]}***")
    print(f"标签: {d['tags']}")
    print(f"分类: {d['category']}")
    print(f"状态: {d['status']}")
    print(f"创建时间: {d['created_at']}")
    print("-" * 40)

print()
print("=" * 60)
print("  议价数据")
print("=" * 60)
rows = conn.execute("SELECT * FROM bargain_data").fetchall()
if not rows:
    print("暂无议价数据")
for r in rows:
    d = dict(r)
    print(f"商品: {d['item_name']}  (ID: {d['item_id']})")
    print(f"  标价: ¥{d['asking_price']}, 底价: ¥{d['seller_min_price']}")
    print(f"  模拟总次数: {d['sim_count']} (成交: {d['sim_deal_count']}, 失败: {d['sim_fail_count']})")
    print(f"  模拟成交价: {d['sim_prices']}")
    print(f"  真实成交价: {d['real_prices']}")
    print()

conn.close()
