"""
多轮实验汇总工具
==============
运行 N 轮实验，取平均值和标准差，生成带误差棒的图表数据。

用法：
  python poster_multi_run.py [轮数]

示例：
  python poster_multi_run.py          # 默认 10 轮
  python poster_multi_run.py 20       # 20 轮

输出：
  poster_multi_learning.csv     — 平均学习曲线（带标准差）
  poster_multi_confidence.csv   — 平均置信度对比（带标准差）
  poster_multi_accept_prob.csv  — 平均接受概率曲线（带标准差）
"""

import sys, os, math, csv, subprocess, random
OUT_DIR = os.path.join(os.path.dirname(__file__), "poster_data")
from collections import defaultdict

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from api.price_learning import PriceLearner, CategoryPriceLearner, learner_from_db_record
from api.bargain_data import get_all_bargain_items
from api.database import get_connection


def run_single_experiment():
    """运行一轮实验并返回所有商品的议价记录"""
    # 清空并生成数据
    conn = get_connection()
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM tag_stats")
    conn.execute("DELETE FROM bargain_data")
    conn.commit()
    conn.close()

    import generate_demo_data
    generate_demo_data.create_demo_data()

    return get_all_bargain_items()


def extract_metrics(records):
    """从一轮实验结果中提取关键指标"""
    # 1. 全局统计
    total_sims = sum(r.get("sim_count", 0) for r in records)
    total_deals = sum(r.get("sim_deal_count", 0) for r in records)
    deal_rate = total_deals / total_sims * 100 if total_sims > 0 else 0

    all_prices = []
    for r in records:
        all_prices.extend(r.get("sim_prices", []))
        all_prices.extend(r.get("real_prices", []))
    avg_price = sum(all_prices) / len(all_prices) if all_prices else 0

    # 2. 各分类统计
    from collections import defaultdict
    cat_stats = defaultdict(lambda: {"sims": 0, "deals": 0, "prices": []})
    for r in records:
        c = r.get("category", "未分类")
        cat_stats[c]["sims"] += r.get("sim_count", 0)
        cat_stats[c]["deals"] += r.get("sim_deal_count", 0)
        cat_stats[c]["prices"].extend(r.get("sim_prices", []))

    # 3. 学习效果（固定测试商品：双肩包）
    test_learning = {}
    for r in records:
        if r["item_name"] == "双肩包" and r.get("sim_prices"):
            l = learner_from_db_record(r)
            s = l.suggest()
            prior = (r["asking_price"] + r["seller_min_price"]) / 2
            test_learning = {
                "prior_est": round(prior),
                "final_est": s["expected_price"],
                "confidence": s["confidence"],
                "deals": len(r["sim_prices"]),
            }
            break

    # 4. 冷启动置信度
    cold_start = {}
    for cat in ["书籍文具", "数码", "服饰个护", "生活家居"]:
        cl = CategoryPriceLearner(cat)
        sc = cl.suggest_for_new_item(88, 52)
        if sc.get("has_category_data"):
            cold_start[cat] = sc["confidence"]

    # 5. 接受概率曲线数据
    accept_probs = []
    demo = None
    for r in records:
        if 40 <= r.get("asking_price", 0) <= 60 and r.get("sim_count", 0) >= 3:
            demo = r
            break
    if demo:
        l = learner_from_db_record(demo)
        for offset in range(-20, 21, 2):
            prob = l.acceptance_probability(demo["asking_price"] + offset)
            accept_probs.append({"offset": offset, "prob": prob})

    return {
        "deal_rate": deal_rate,
        "avg_price": avg_price,
        "total_sims": total_sims,
        "cat_stats": dict(cat_stats),
        "test_learning": test_learning,
        "cold_start": cold_start,
        "accept_probs": accept_probs,
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    NUM_RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print("=" * 70)
    print(f"  多轮实验汇总工具（{NUM_RUNS} 轮）")
    print("=" * 70)

    all_metrics = []
    for i in range(NUM_RUNS):
        print(f"\n  第 {i+1}/{NUM_RUNS} 轮...", end=" ", flush=True)
        records = run_single_experiment()
        metrics = extract_metrics(records)
        all_metrics.append(metrics)
        print(f"成交率 {metrics['deal_rate']:.0f}%  均价 ¥{metrics['avg_price']:.0f}")

    # ── 计算平均值和标准差 ──
    def avg_std(values):
        n = len(values)
        mean = sum(values) / n
        var = sum((v - mean)**2 for v in values) / n
        return mean, math.sqrt(var)

    print(f"\n{'='*70}")
    print(f"  汇总结果（{NUM_RUNS} 轮平均）")
    print(f"{'='*70}")

    # 全局指标
    rates = [m["deal_rate"] for m in all_metrics]
    prices = [m["avg_price"] for m in all_metrics]
    sims = [m["total_sims"] for m in all_metrics]

    m_rate, s_rate = avg_std(rates)
    m_price, s_price = avg_std(prices)
    m_sims, _ = avg_std(sims)

    print(f"\n  全局指标:")
    print(f"    平均成交率:  {m_rate:.1f}% ± {s_rate:.1f}")
    print(f"    平均成交价:  ¥{m_price:.1f} ± {s_price:.1f}")
    print(f"    平均模拟数:  {m_sims:.0f} 次")

    # 导出 CSV
    path = os.path.join(OUT_DIR, "poster_multi_learning.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["=== 全局统计（N轮平均） ==="])
        w.writerow(["指标", "平均值", "标准差", "单位"])
        w.writerow(["成交率", round(m_rate, 1), round(s_rate, 1), "%"])
        w.writerow(["均价", round(m_price, 1), round(s_price, 1), "元"])
        w.writerow(["每轮模拟次数", round(m_sims, 0), "", "次"])

        # 学习效果
        w.writerow([])
        w.writerow(["=== 学习效果（双肩包） ==="])
        w.writerow(["指标", "平均值", "标准差"])
        prior_ests = [m["test_learning"]["prior_est"] for m in all_metrics if m["test_learning"]]
        final_ests = [m["test_learning"]["final_est"] for m in all_metrics if m["test_learning"]]
        confs = [m["test_learning"]["confidence"] for m in all_metrics if m["test_learning"]]
        deals_n = [m["test_learning"]["deals"] for m in all_metrics if m["test_learning"]]

        if prior_ests:
            mp, sp = avg_std(prior_ests)
            mf, sf = avg_std(final_ests)
            mc, sc = avg_std(confs)
            md, _ = avg_std(deals_n)
            w.writerow(["初次估值（仅先验）", round(mp, 1), round(sp, 1)])
            w.writerow(["最终估值", round(mf, 1), round(sf, 1)])
            w.writerow(["置信度", round(mc, 3), round(sc, 3)])
            w.writerow(["数据笔数", round(md, 0), ""])
            print(f"\n  学习效果（双肩包）:")
            print(f"    估值: {mp:.0f} → {mf:.0f}  (±{sf:.1f})")
            print(f"    置信度: {mc:.2f}  (±{sc:.2f})")

        # 冷启动置信度
        w.writerow([])
        w.writerow(["=== 冷启动置信度对比 ==="])
        w.writerow(["场景", "平均置信度", "标准差"])

        print(f"\n  冷启动置信度:")
        for cat in ["书籍文具", "数码", "服饰个护", "生活家居"]:
            vals = [m["cold_start"].get(cat, 0) for m in all_metrics]
            if vals:
                mv, sv = avg_std(vals)
                w.writerow([f"借用{cat}类", round(mv, 3), round(sv, 3)])
                print(f"    {cat}: {mv:.2f} (±{sv:.2f})")

        # 接受概率曲线（跨实验汇总，计算平均+标准差）
        w.writerow([])
        w.writerow(["=== 接受概率曲线（平均） ==="])
        w.writerow(["出价偏移(元)", "平均接受概率", "标准差"])

        # 收集所有实验的 accept_probs
        all_probs = [m.get("accept_probs", []) for m in all_metrics if m.get("accept_probs")]
        if all_probs:
            n_exps = len(all_probs)
            for idx in range(len(all_probs[0])):
                vals = [exp[idx]["prob"] for exp in all_probs if idx < len(exp)]
                if vals:
                    mean = sum(vals) / len(vals)
                    var = sum((v - mean)**2 for v in vals) / len(vals)
                    std = math.sqrt(var)
                    w.writerow([all_probs[0][idx]["offset"], round(mean, 4), round(std, 4)])

    print(f"\n  ✅ {path} （汇总数据，可直接用于 Poster）")
    print(f"\n{'='*70}")
    print(f"  完成！{NUM_RUNS} 轮实验结果已汇总。")
    print(f"  poster_multi_learning.csv 包含平均值和标准差，")
    print(f"  可用 Excel 画带误差棒的图表。")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
