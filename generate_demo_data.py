"""
清小闲 — 演示数据生成器（Poster 用）
====================================

核心逻辑：严格按照真实议价流程模拟多轮谈判。
每个商品依次进行多次议价，每次都用真实的 PriceLearner 决策。

流程：
  1. 初始化 PriceLearner(标价, 底价) → 纯先验
  2. 模拟第 1 次议价对话（用户 vs AI 卖家）
     - 用户出价 → AI 按规则还价 → 多轮 → 成交/失败
     - record_sim_deal/fail → 数据写入数据库
     - learner.add_sim_deal/fail → 算法参数更新
  3. 模拟第 2 次议价（此时 PriceLearner 已有 1 条数据）
     - 议价策略因经验变化而不同
  4. ...重复直到达到设定的会话次数
  5. 最终状态：PriceLearner 积累了多条经验，估值和置信度更准确

输出：
  - 数据写入 SQLite 数据库
  - 统计报告打印到终端
  - chart_data.csv 供 Excel 画图
"""

import sys, os, math, random, json, csv
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from api.database import init_db, get_connection, add_item, record_tags
from api.bargain_data import init_bargain_table, record_sim_deal, record_sim_fail, \
    record_real_deal, get_global_stats
from api.price_learning import PriceLearner, CategoryPriceLearner, learner_from_db_record

random.seed(42)

# ═══════════════════════════════════════════════════════════
# 商品模板
# ═══════════════════════════════════════════════════════════

ITEMS_BY_CATEGORY = {
    "书籍文具": [
        ("高等数学(上)", 40, 28), ("线性代数", 35, 24), ("C程序设计", 45, 32),
        ("数据结构", 55, 39), ("大学物理", 50, 35), ("考研英语真题", 60, 42),
        ("四级词汇书", 25, 18), ("笔记本3本装", 15, 11), ("全新小说《三体》", 30, 21),
    ],
    "数码": [
        ("AirPods Pro", 800, 560), ("小米充电宝20000mAh", 70, 49),
        ("罗技鼠标", 120, 84), ("iPad保护壳", 40, 28),
        ("USB-C扩展坞", 65, 46), ("二手Kindle", 350, 245),
        ("手机支架", 20, 14), ("蓝牙音箱", 150, 105),
    ],
    "生活家居": [
        ("台灯(护眼)", 60, 42), ("折叠椅", 45, 32), ("床垫(90cm)", 120, 84),
        ("电风扇", 80, 56), ("收纳箱三层", 35, 25), ("保温杯", 30, 21),
    ],
    "服饰个护": [
        ("卫衣(均码)", 80, 56), ("运动鞋42码", 150, 105), ("双肩包", 100, 70),
        ("防晒霜", 35, 25), ("电动牙刷", 90, 63), ("棒球帽", 25, 18),
    ],
    "运动出行": [
        ("自行车(通勤)", 300, 210), ("羽毛球拍", 120, 84), ("瑜伽垫", 40, 28),
        ("滑板", 150, 105), ("跳绳", 15, 11), ("护膝", 35, 25),
    ],
    "娱乐休闲": [
        ("木吉他入门", 300, 210), ("三国杀桌游", 40, 28), ("手办(初音)", 180, 126),
        ("口琴", 60, 42), ("数位板", 200, 140), ("耳机架", 25, 18),
    ],
}


# ═══════════════════════════════════════════════════════════
# 商品描述
# ═══════════════════════════════════════════════════════════

def generate_description(name, category):
    descs = {
        "书籍文具": f"九成新{name}，考研/课程教材，内页干净无笔记，清华校内自取。",
        "数码": f"自用{name}，功能正常无维修，充电附件齐全，寝室自取优先。",
        "生活家居": f"毕业出{name}，用了半年，干净无破损，C楼附近自取。",
        "服饰个护": f"全新/九成新{name}，尺码标准，买多了没用完，清华校内交易。",
        "运动出行": f"毕业季出{name}，正常使用痕迹，适合校园通勤/运动，需自取。",
        "娱乐休闲": f"二手{name}，闲置出掉，功能完好，适合入门学习/娱乐。",
    }
    return descs.get(category, f"二手{name}，价格可小刀，清华校内自取。")


# ═══════════════════════════════════════════════════════════
# 用户类型（决定议价行为）
# ═══════════════════════════════════════════════════════════

USER_TYPES = [
    {
        "name": "aggressive",
        "weight": 25,
        "init_lo": 0.40, "init_hi": 0.55,
        "raise_lo": 0.08, "raise_hi": 0.18,
        "quit_lo": 0.08, "quit_hi": 0.20,
    },
    {
        "name": "moderate",
        "weight": 45,
        "init_lo": 0.55, "init_hi": 0.72,
        "raise_lo": 0.12, "raise_hi": 0.28,
        "quit_lo": 0.03, "quit_hi": 0.10,
    },
    {
        "name": "generous",
        "weight": 30,
        "init_lo": 0.68, "init_hi": 0.88,
        "raise_lo": 0.18, "raise_hi": 0.35,
        "quit_lo": 0.00, "quit_hi": 0.05,
    },
]


# ═══════════════════════════════════════════════════════════
# 核心：模拟一次完整的议价对话
# ═══════════════════════════════════════════════════════════

def simulate_one_bargain(learner, asking, min_price):
    """
    模拟一次完整的议价对话（多轮讨价还价）。

    参数:
      learner: PriceLearner 实例（已包含此前的交易经验）
      asking: 标价
      min_price: 底价

    返回:
      (outcome_type, price_or_offer, counter_or_reason)
      - ("deal", deal_price, _)
      - ("fail", buyer_offer, seller_counter)
      - ("fail", buyer_offer, "seller_rejected")
      - ("fail", buyer_offer, "buyer_declined")
      - ("fail", buyer_offer, "timeout")
    """
    # 选一个用户类型（加权随机）
    user_t = random.choices(USER_TYPES, weights=[u["weight"] for u in USER_TYPES])[0]

    # 获取算法当前的建议（反映了此前的交易经验）
    suggestion = learner.suggest()
    anchor = suggestion["expected_price"]   # 心理锚点
    confidence = suggestion["confidence"]

    MAX_ROUNDS = 5
    user_offer = None        # 用户当前出价
    seller_price = asking    # AI 当前要价（从标价开始降）

    for round_i in range(1, MAX_ROUNDS + 1):
        # ────────────────────────────────────
        # 用户出价
        # ────────────────────────────────────
        if user_offer is None:
            # 首次出价：根据用户类型在范围内随机
            init_pct = random.uniform(user_t["init_lo"], user_t["init_hi"])
            user_offer = max(1, round(asking * init_pct))
        else:
            # 后续出价：在 AI 还价和用户当前出价之间往上抬
            gap = seller_price - user_offer
            if gap <= 0:
                # 用户出价已经达到 AI 要价 → 成交
                return ("deal", user_offer, seller_price)
            raise_pct = random.uniform(user_t["raise_lo"], user_t["raise_hi"])
            user_offer = round(user_offer + gap * raise_pct)

        # 用户可能放弃
        quit_prob = random.uniform(user_t["quit_lo"], user_t["quit_hi"])
        if random.random() < quit_prob:
            return ("fail", user_offer, "buyer_declined")

        # ────────────────────────────────────
        # AI 评估出价
        # ────────────────────────────────────

        # 【规则B】低于底价 → 铁律拒绝
        if user_offer < min_price:
            return ("fail", user_offer, "seller_rejected")

        # 【规则D-1】出价 ≥ 锚点 → 可能接受（价格合理）
        if user_offer >= anchor:
            # 置信度越高，AI 对锚点越有信心
            accept_chance = 0.65 + confidence * 0.3
            if random.random() < accept_chance:
                return ("deal", user_offer, seller_price)

        # 出价接近锚点（90%以上）→ 可能接受
        if user_offer >= anchor * 0.9:
            if random.random() < 0.35:
                return ("deal", user_offer, seller_price)

        # 出价 ≥ 标价 90% → 直接接受
        if user_offer >= asking * 0.9:
            return ("deal", user_offer, seller_price)

        # 用 acceptance_probability 辅助决策
        prob = learner.acceptance_probability(user_offer)
        if prob > 0.4 and random.random() < prob:
            return ("deal", user_offer, seller_price)

        # ────────────────────────────────────
        # AI 还价
        # ────────────────────────────────────

        if round_i == 1:
            # 第一次还价：降不超过 5%
            seller_price = round(asking * 0.95)

        elif round_i == 2:
            # 第二次还价：累计降不超过 12%
            seller_price = round(asking * 0.88)

        elif round_i == 3:
            # 第三次还价：累计降不超过 20%
            seller_price = round(asking * 0.80)

        else:
            if round_i == 4:
                # 第4轮：再降一些，贴近锚点
                seller_price = max(round(anchor * 0.95), round(asking * 0.75))
            else:
                # 第5轮：最后一口价，折中方案
                seller_price = round((anchor + min_price) / 2)

            if round_i >= MAX_ROUNDS:
                if user_offer >= seller_price:
                    return ("deal", user_offer, seller_price)
                elif user_offer >= seller_price * 0.9 and random.random() < 0.3:
                    return ("deal", user_offer, seller_price)
                else:
                    return ("fail", user_offer, "seller_held_firm")

        # 【规则C】还价不能低于用户当前出价
        seller_price = max(seller_price, user_offer + 2)

    # 超过最大轮数→超时
    return ("fail", user_offer, "timeout")


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════

def create_demo_data():
    LOCAL = os.path.dirname(os.path.abspath(__file__))

    # 清空数据库
    conn = get_connection()
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM tag_stats")
    conn.execute("DELETE FROM bargain_data")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print("✅ 数据库已清空")

    init_db()
    init_bargain_table()
    print("✅ 表已初始化")

    idx = 0
    total_items = 0
    total_deals = 0
    total_fails = 0
    total_reals = 0
    all_item_stats = {}

    for category, items in ITEMS_BY_CATEGORY.items():
        for name, asking, min_price in items:
            idx += 1
            item_id = f"ITEM-20260701-{idx:04d}"
            now = datetime.now().isoformat()

            # 写入 items 表
            item = {
                "item_id": item_id,
                "name": name,
                "description": generate_description(name, category),
                "price": str(asking),
                "contact_type": "wechat",
                "contact_value": "demo_user",
                "category": category,
                "tags": [category],
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            add_item(item)
            record_tags([category])
            total_items += 1

            # 初始化议价记录
            from api.bargain_data import init_item_bargain
            init_item_bargain(item_id, name, asking, min_price, category)

            # ── 创建 PriceLearner，开始模拟议价 ──
            learner = PriceLearner(asking, min_price)

            item_deals = []
            item_fails = []
            item_reals = []

            # 每个商品进行 3~7 次独立的议价会话
            num_sessions = random.randint(3, 7)

            for sess in range(num_sessions):
                # 模拟一次完整的议价对话
                outcome = simulate_one_bargain(learner, asking, min_price)

                if outcome[0] == "deal":
                    deal_price = outcome[1]
                    # 记录到数据库
                    record_sim_deal(item_id, deal_price)
                    # 更新学习器（影响后续会话）
                    learner.add_sim_deal(deal_price)
                    item_deals.append(deal_price)
                    total_deals += 1

                    # 部分商品在模拟成交后有人报告真实成交（20%概率）
                    if random.random() < 0.20:
                        real_price = round(deal_price + random.uniform(-5, 5))
                        real_price = max(min_price, min(asking, real_price))
                        record_real_deal(item_id, real_price)
                        learner.add_real_deal(real_price)
                        item_reals.append(real_price)
                        total_reals += 1

                else:
                    # 失败
                    buyer_offer = outcome[1]
                    reason = outcome[2]

                    if reason == "seller_rejected":
                        record_sim_fail(item_id, buyer_offer, min_price, "seller_rejected")
                        learner.add_sim_fail(buyer_offer, min_price, "seller_rejected")
                    elif reason == "seller_held_firm":
                        # 最后一口价被拒
                        final_price = max(learner.suggest()["expected_price"],
                                          round(asking * 0.75))
                        record_sim_fail(item_id, buyer_offer, final_price, "seller_held_firm")
                        learner.add_sim_fail(buyer_offer, final_price, "seller_held_firm")
                    elif reason == "buyer_declined":
                        record_sim_fail(item_id, buyer_offer, 0, "buyer_declined")
                        learner.add_sim_fail(buyer_offer, 0, "buyer_declined")
                    else:  # timeout
                        record_sim_fail(item_id, buyer_offer, 0, "timeout")
                        learner.add_sim_fail(buyer_offer, 0, "timeout")

                    item_fails.append((reason, buyer_offer))
                    total_fails += 1

            # 记录最终统计
            all_item_stats[item_id] = {
                "name": name,
                "category": category,
                "asking": asking,
                "min": min_price,
                "deals": item_deals,
                "fails": item_fails,
                "reals": item_reals,
                "final_confidence": learner.suggest()["confidence"],
                "final_estimate": learner.suggest()["expected_price"],
                "num_sessions": num_sessions,
            }

    print(f"\n✅ 数据生成完成！")
    print(f"   {total_items} 件商品")
    print(f"   {total_deals} 次模拟成交")
    print(f"   {total_fails} 次模拟失败")
    print(f"   {total_reals} 条真实成交")
    return all_item_stats


def analyze_and_report(all_item_stats):
    """分析并输出统计报告"""
    records = get_all_bargain_items() if 'get_all_bargain_items' in dir() else []
    from api.bargain_data import get_all_bargain_items

    records = get_all_bargain_items()
    if not records:
        print("❌ 没有数据")
        return

    print("\n" + "=" * 70)
    print("  数据统计报告")
    print("=" * 70)

    gs = get_global_stats()
    print(f"\n📊 全局概览:")
    print(f"   商品数: {gs['total_items_with_bargain_data']} 件")
    print(f"   总模拟: {gs['total_simulations']} 次")
    print(f"   成交率: {gs['sim_deal_rate']}%")
    print(f"   模拟均价: ¥{gs['avg_sim_price']:.1f}" if gs.get('avg_sim_price') else "")
    print(f"   真实成交: {gs['total_real_transactions_reported']} 笔")

    # 按分类统计
    from collections import defaultdict
    cats = defaultdict(lambda: {"items": 0, "sims": 0, "deals": 0, "fails": 0,
                                 "reals": 0, "prices": [], "final_conf": []})
    for rec in records:
        c = rec.get("category", "未分类")
        cats[c]["items"] += 1
        cats[c]["sims"] += rec.get("sim_count", 0)
        cats[c]["deals"] += rec.get("sim_deal_count", 0)
        cats[c]["fails"] += rec.get("sim_fail_count", 0)
        cats[c]["reals"] += rec.get("real_count", 0)
        cats[c]["prices"].extend(rec.get("sim_prices", []))
        cats[c]["prices"].extend(rec.get("real_prices", []))

    print(f"\n📂 按分类统计:")
    print(f"   {'分类':<12} {'商品':>4} {'会话':>6} {'成交':>4} {'失败':>4} {'真实':>4} {'均价':>7} {'成交率':>6}")
    print(f"   {'-'*50}")
    for c, d in sorted(cats.items()):
        avg = sum(d["prices"])/len(d["prices"]) if d["prices"] else 0
        rate = d["deals"]/d["sims"]*100 if d["sims"] else 0
        print(f"   {c:<12} {d['items']:>4} {d['sims']:>6} {d['deals']:>4} {d['fails']:>4} "
              f"{d['reals']:>4} ¥{avg:>5.1f} {rate:>5.0f}%")

    # PriceLearner 学习效果抽样
    print(f"\n🧠 PriceLearner 学习效果（每个分类选一例）:")
    print(f"   {'分类':<12} {'商品':<18} {'标价':>5} {'底价':>5} {'初次估值':>8} {'最终估值':>8} {'置信度':>7} {'会话':>4}")
    print(f"   {'-'*65}")

    best_per_cat = {}
    for rec in records:
        c = rec.get("category", "未分类")
        l = learner_from_db_record(rec)
        s = l.suggest()
        n_deals = len(rec.get("sim_prices", []))
        if c not in best_per_cat or n_deals > best_per_cat[c]["deals"]:
            # 计算初次估值（仅先验）
            prior_est = (rec["asking_price"] + rec["seller_min_price"]) / 2
            best_per_cat[c] = {
                "name": rec["item_name"],
                "asking": rec["asking_price"],
                "min": rec["seller_min_price"],
                "prior": round(prior_est),
                "final": s["expected_price"],
                "conf": s["confidence"],
                "deals": n_deals,
            }

    for c, d in sorted(best_per_cat.items()):
        print(f"   {c:<12} {d['name']:<18} {d['asking']:>5} {d['min']:>5} "
              f"{d['prior']:>8} {d['final']:>8} {d['conf']:>6.1%} {d['deals']:>4}笔")

    # 冷启动效果
    print(f"\n❄️ 冷启动效果（新商品《离散数学》标价88底价52）:")
    from api.price_learning import CategoryPriceLearner
    lp = PriceLearner(88, 52)
    sp = lp.suggest()
    print(f"   纯先验: 估值={sp['expected_price']:.0f}  置信度={sp['confidence']:.2f}")

    for cat in ["书籍文具", "数码", "生活家居", "服饰个护", "运动出行", "娱乐休闲"]:
        cl = CategoryPriceLearner(cat)
        sc = cl.suggest_for_new_item(88, 52)
        if sc.get("has_category_data"):
            boost = (sc["confidence"] - sp["confidence"]) / sp["confidence"] * 100
            print(f"   借用{cat}: 估值={sc['expected_price']:.0f}  置信度={sc['confidence']:.2f} (↑{boost:.0f}%)")

    # 导出 CSV
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["=== 各分类成交价分布 ==="])
        w.writerow(["分类", "商品数", "会话次数", "成交", "失败", "真实", "平均成交价", "成交率"])
        for c, d in sorted(cats.items()):
            avg = sum(d["prices"])/len(d["prices"]) if d["prices"] else 0
            rate = d["deals"]/d["sims"]*100 if d["sims"] else 0
            w.writerow([c, d["items"], d["sims"], d["deals"], d["fails"], d["reals"],
                        round(avg, 1), f"{rate:.0f}%"])

        w.writerow([])
        w.writerow(["=== PriceLearner 学习效果 ==="])
        w.writerow(["分类", "商品", "标价", "底价", "初次估值(仅先验)", "最终估值", "置信度", "数据笔数"])
        for c, d in sorted(best_per_cat.items()):
            w.writerow([c, d["name"], d["asking"], d["min"], d["prior"],
                       d["final"], d["conf"], d["deals"]])

        w.writerow([])
        w.writerow(["=== 冷启动置信度对比 ==="])
        w.writerow(["场景", "估值", "置信度", "提升"])
        w.writerow(["纯先验（无数据）", sp['expected_price'], sp['confidence'], "-"])
        for cat in ["书籍文具", "数码", "生活家居", "服饰个护", "运动出行", "娱乐休闲"]:
            cl = CategoryPriceLearner(cat)
            sc = cl.suggest_for_new_item(88, 52)
            if sc.get("has_category_data"):
                boost = (sc["confidence"] - sp["confidence"]) / sp["confidence"] * 100
                w.writerow([f"借用{cat}类", sc['expected_price'], sc['confidence'], f"+{boost:.0f}%"])

    print(f"\n📁 图表数据已导出: chart_data.csv")


if __name__ == "__main__":
    print("=" * 70)
    print("  清小闲 — 演示数据生成器")
    print("  （严格模拟多轮议价，PriceLearner 实时更新）")
    print("=" * 70)

    # 检查是否带 --force 参数
    if "--force" not in sys.argv:
        print("\n  ⚠️  此操作将清空所有现有数据（包括真实测试数据）")
        print("  并重新生成 41 件虚拟商品 + 200+ 次模拟交易。")
        ans = input("  确认？输入 YES 继续: ").strip()
        if ans != "YES":
            print("\n  ❌ 已取消")
            sys.exit(0)

    stats = create_demo_data()
    analyze_and_report(stats)

    print(f"\n{'='*70}")
    print(f"  ✅ 完成！")
    print(f"     python backend/main.py  # 启动服务器")
    print(f"     python generate_demo_data.py --force  # 跳过确认直接跑")
    print(f"     chart_data.csv 可导入 Excel 画图")
    print(f"{'='*70}")
