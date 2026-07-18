"""
Poster 数据可视化工具
====================
在 generate_demo_data.py 之后运行，输出 4 个 CSV 文件，
每个对应一种图表类型，可直接导入 Excel/Matplotlib 画图。

用法：
  python generate_demo_data.py --force
  python poster_charts.py

输出：
  poster_chart_1_learning.csv  — 学习曲线（折线图）
  poster_chart_2_confidence.csv — 冷启动对比（柱状图）
  poster_chart_3_categories.csv — 分类价格分布（箱线图/柱状图）
  poster_chart_4_accept_prob.csv — 接受概率（Sigmoid 曲线）
"""

import sys, os, math, csv, random

# 输出目录
OUT_DIR = os.path.join(os.path.dirname(__file__), "poster_data")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from api.price_learning import PriceLearner, CategoryPriceLearner, learner_from_db_record
from api.bargain_data import get_all_bargain_items


def collect_data():
    """从数据库收集所有需要的数据"""
    records = get_all_bargain_items()
    if not records:
        print("❌ 数据库为空，请先运行 python generate_demo_data.py --force")
        return None
    return records


def export_learning_curve(records):
    """
    图表 1：学习曲线（折线图）
    ------------------------------------
    X 轴：议价会话次数（第几次议价）
    Y 轴：预估成交价（estimated_mean）
    每条线：一个商品

    选 3 个不同价位的商品展示学习过程。
    展示随着议价次数增加，估值如何从先验收敛到真实市场价。
    """
    # 选 3 个有成交记录的商品：便宜/中等/贵
    targets = ["高等数学(上)", "双肩包", "木吉他入门"]
    rows = [["商品", "标价", "底价", "会话编号", "该次后估值", "该次后置信度", "是否成交"]]

    for rec in records:
        name = rec["item_name"]
        if name not in targets:
            continue

        asking = rec["asking_price"]
        min_p = rec["seller_min_price"]
        sims = rec.get("sim_prices", [])
        fails = rec.get("sim_fails", [])
        reals = rec.get("real_prices", [])

        # 逐个数据点重建学习过程
        learner = PriceLearner(asking, min_p)
        prior_est = (asking + min_p) / 2
        rows.append([name, asking, min_p, 0, round(prior_est), 0.23, "初始先验"])

        # 按时间顺序：先模拟成交，再失败，再真实（按记录顺序）
        all_events = []
        for p in sims:
            all_events.append(("sim_deal", p))
        for f in fails:
            all_events.append(("sim_fail", f["buyer_offer"]))
        for p in reals:
            all_events.append(("real_deal", p))

        for i, (typ, val) in enumerate(all_events, 1):
            if typ == "sim_deal":
                learner.add_sim_deal(val)
            elif typ == "real_deal":
                learner.add_real_deal(val)
            else:
                continue  # 失败不影响估值，只影响下界

            s = learner.suggest()
            outcome = "成交" if typ == "sim_deal" else "真实" if typ == "real_deal" else "失败"
            rows.append([name, asking, min_p, i, s["expected_price"],
                         s["confidence"], outcome])

    path = os.path.join(OUT_DIR, "poster_chart_1_learning.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"  ✅ poster_data/ 目录下文件已生成")
    print(f"  ✅ {path} （折线图用——X=会话编号, Y=估值, 不同商品不同线条）")
    print(f"     展示：估值随议价次数收敛到市场价")


def export_confidence_comparison(records):
    """
    图表 2：冷启动置信度对比（柱状图）
    ------------------------------------
    X 轴：不同场景
    Y 轴：置信度

    对比 4 种场景：
    1. 纯先验（无数据）
    2. 借用分类数据
    3. 有 1-2 笔交易
    4. 有 5+ 笔交易

    展示数据越多置信度越高。
    """
    asking, min_p = 88, 52  # 标准测试商品

    rows = [["场景", "置信度", "估值(元)", "数据笔数"]]

    # 场景 1：纯先验
    lp = PriceLearner(asking, min_p)
    sp = lp.suggest()
    rows.append(["纯先验（无数据）", sp["confidence"], sp["expected_price"], 0])

    # 场景 2：借用分类数据
    for cat in ["书籍文具", "数码", "服饰个护"]:
        cl = CategoryPriceLearner(cat)
        sc = cl.suggest_for_new_item(asking, min_p)
        if sc.get("has_category_data"):
            rows.append([f"借用{cat}类", sc["confidence"],
                        sc["expected_price"], sc["category_items"]])

    # 场景 3-4：从真实商品中抽样
    for rec in records:
        n_sims = len(rec.get("sim_prices", []))
        if n_sims in [1, 2, 5, 6]:
            l2 = learner_from_db_record(rec)
            s2 = l2.suggest()
            label = f"有{n_sims}笔交易"
            rows.append([label, s2["confidence"], s2["expected_price"], n_sims])

    path = os.path.join(OUT_DIR, "poster_chart_2_confidence.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"  ✅ {path} （柱状图用——X=场景, Y=置信度）")
    print(f"     展示：纯先验0.23 → 借用分类0.7+ → 有数据后更高")


def export_category_prices(records):
    """
    图表 3：各分类价格分布（箱线图/柱状图）
    ------------------------------------
    X 轴：分类
    Y 轴：成交价

    展示不同分类的价格区间和均值差异，
    说明跨品类价格学习的必要性。
    """
    from collections import defaultdict
    cat_data = defaultdict(list)

    for rec in records:
        c = rec.get("category", "未分类")
        for p in rec.get("sim_prices", []):
            cat_data[c].append(p)
        for p in rec.get("real_prices", []):
            cat_data[c].append(p)

    # CSV 格式：每条数据一行，方便画箱线图
    rows = [["分类", "成交价"]]
    for c, prices in sorted(cat_data.items()):
        for p in prices:
            rows.append([c, p])

    # 再加一行汇总
    rows.append([])
    rows.append(["分类", "商品数", "总成交数", "均价", "最低", "最高", "中位数"])
    for c, prices in sorted(cat_data.items()):
        if prices:
            sorted_p = sorted(prices)
            n_items = sum(1 for r in records if r.get("category") == c)
            rows.append([c, n_items, len(prices), round(sum(prices)/len(prices), 1),
                        min(prices), max(prices),
                        sorted_p[len(sorted_p)//2]])

    path = os.path.join(OUT_DIR, "poster_chart_3_categories.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"  ✅ {path} （箱线图/柱状图用——X=分类, Y=成交价）")
    print(f"     展示：不同品类价格差异大，需要分类学习")


def export_accept_probability():
    """
    图表 4：接受概率曲线（Sigmoid 曲线）
    ------------------------------------
    X 轴：出价（相对估值的偏移量）
    Y 轴：接受概率

    画 3 条曲线对比：
    1. 无数据时（k=0.04，平缓）
    2. 有少量数据（k=0.07）
    3. 有大量数据（k=0.10，陡峭）

    展示数据量如何影响卖家坚定程度。
    """
    rows = [["出价偏移(元)", "无数据(k=0.04)", "少量数据(k=0.07)", "大量数据(k=0.10)"]]

    for offset in range(-30, 31, 2):
        row = [offset]
        for k in [0.04, 0.07, 0.10]:
            prob = 1.0 / (1.0 + math.exp(-k * offset))
            row.append(round(prob, 3))
        rows.append(row)

    # 再加一个不同数据量下 acceptance_probability 的真实案例
    rows.append([])
    rows.append(["offer", "无数据时的P(accept)", "有3条数据的P(accept)",
                 "有6条数据的P(accept)"])

    # 从数据库中找一个成交价 50 左右的商品做演示
    records = get_all_bargain_items()
    demo = None
    for rec in records:
        if 40 <= rec.get("asking_price", 0) <= 60 and rec.get("sim_count", 0) >= 3:
            demo = rec
            break

    if demo:
        for offer in range(20, int(demo["asking_price"]) + 1, 2):
            row = [offer]

            # 无数据
            l0 = PriceLearner(demo["asking_price"], demo["seller_min_price"])
            row.append(l0.acceptance_probability(offer))

            # 有 3 条数据
            l3 = PriceLearner(demo["asking_price"], demo["seller_min_price"])
            for p in demo.get("sim_prices", [])[:3]:
                l3.add_sim_deal(p)
            row.append(l3.acceptance_probability(offer))

            # 有 6 条数据
            l6 = PriceLearner(demo["asking_price"], demo["seller_min_price"])
            for p in demo.get("sim_prices", [])[:6]:
                l6.add_sim_deal(p)
            row.append(l6.acceptance_probability(offer))

            rows.append(row)

    path = os.path.join(OUT_DIR, "poster_chart_4_accept_prob.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"  ✅ {path} （Sigmoid 曲线用——X=出价偏移, Y=接受概率）")
    print(f"     展示：数据越多曲线越陡，卖家越坚定")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("=" * 70)
    print("  Poster 数据可视化工具")
    print("=" * 70)

    records = collect_data()
    if not records:
        return

    print(f"\n基于 {len(records)} 件商品的议价数据，生成 4 张图表数据:\n")

    export_learning_curve(records)
    export_confidence_comparison(records)
    export_category_prices(records)
    export_accept_probability()

    print(f"\n{'='*70}")
    print(f"  4 个 CSV 文件已生成！用 Excel 打开:")
    print(f"  ┌──────────────────────────────┬──────────────────────────────┐")
    print(f"  │ CSV 文件                      │ Excel 操作                   │")
    print(f"  ├──────────────────────────────┼──────────────────────────────┤")
    print(f"  │ poster_chart_1_learning.csv  │ 插入→图表→折线图              │")
    print(f"  │                              │ X=会话编号  Y=估值            │")
    print(f"  ├──────────────────────────────┼──────────────────────────────┤")
    print(f"  │ poster_chart_2_confidence.csv│ 插入→图表→柱状图              │")
    print(f"  │                              │ X=场景  Y=置信度              │")
    print(f"  ├──────────────────────────────┼──────────────────────────────┤")
    print(f"  │ poster_chart_3_categories.csv│ 插入→图表→箱线图/柱状图       │")
    print(f"  │                              │ X=分类  Y=成交价              │")
    print(f"  ├──────────────────────────────┼──────────────────────────────┤")
    print(f"  │ poster_chart_4_accept_prob.csv│ 插入→图表→带平滑线的散点图    │")
    print(f"  │                              │ X=出价偏移  Y=概率            │")
    print(f"  └──────────────────────────────┴──────────────────────────────┘")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
