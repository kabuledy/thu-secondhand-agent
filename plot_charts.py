"""
Poster 图表生成器
===============
读取 poster_data/ 下的 CSV，输出 4 张可直接放入 Poster 的 PNG 图。

用法：
  python generate_demo_data.py --force
  python plot_charts.py

依赖：
  pip install matplotlib

输出（在 poster_data/ 目录下）：
  fig1_learning_curve.png    — 学习曲线（折线图）
  fig2_cold_start.png        — 冷启动置信度对比（柱状图）
  fig3_category_prices.png   — 分类价格分布（柱状图）
  fig4_accept_prob.png       — 接受概率 Sigmoid 曲线
"""

import os, csv, math
import matplotlib
matplotlib.use("Agg")  # 无显示器模式
import matplotlib.pyplot as plt
import numpy as np

# ── 输出目录 ──
OUT_DIR = os.path.join(os.path.dirname(__file__), "poster_data")
os.makedirs(OUT_DIR, exist_ok=True)

# ── 图表通用设置 ──
# 中文字体
plt.rcParams.update({
    "font.size": 14,
    "axes.titlesize": 18,
    "axes.labelsize": 15,
    "legend.fontsize": 13,
    "figure.facecolor": "white",
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
    "font.family": ["Microsoft YaHei", "SimHei", "sans-serif"],
})

# 颜色方案（学术风格）
COLORS = ["#2E86AB", "#A23B72", "#F18F01", "#C73E1D", "#3B1F2B", "#44BBA4"]
COLOR_MAP = {
    "书籍文具": COLORS[0],
    "数码": COLORS[1],
    "生活家居": COLORS[2],
    "服饰个护": COLORS[3],
    "运动出行": COLORS[4],
    "娱乐休闲": COLORS[5],
}


def read_csv(filename):
    """读取 CSV 文件，返回 (headers, rows)"""
    path = os.path.join(OUT_DIR, filename)
    if not os.path.exists(path):
        print(f"  ⚠️  {path} 不存在，跳过")
        return None, []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if r and not r[0].startswith("===")]
    if not rows:
        return None, []
    return rows[0], rows[1:]


# ═══════════════════════════════════════════════════════════
# 图1：学习曲线
# ═══════════════════════════════════════════════════════════

def plot_learning_curve():
    """学习曲线：不同商品的估值随议价次数收敛"""
    headers, data = read_csv("poster_chart_1_learning.csv")
    if not data:
        return

    fig, ax = plt.subplots(figsize=(8, 5))

    # 按商品分组
    items = {}
    for row in data:
        name = row[0]
        if name not in items:
            items[name] = {"asking": float(row[1]), "sessions": [], "estimates": []}
        items[name]["sessions"].append(int(row[3]))
        items[name]["estimates"].append(float(row[4]))

    for i, (name, info) in enumerate(sorted(items.items())):
        color = COLORS[i % len(COLORS)]
        ax.plot(info["sessions"], info["estimates"],
                marker="o", linewidth=2, color=color, label=name)
        # 标价虚线
        ax.axhline(y=info["asking"], linestyle="--", linewidth=1,
                   color=color, alpha=0.4)

    ax.set_xlabel("Bargaining Sessions")
    ax.set_ylabel("Estimated Price (¥)")
    ax.set_title("PriceLearner Convergence\n(estimated_mean vs. # of sessions)")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.3)

    path = os.path.join(OUT_DIR, "fig1_learning_curve.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════
# 图2：冷启动置信度对比
# ═══════════════════════════════════════════════════════════

def plot_confidence_comparison():
    """冷启动置信度柱状图"""
    headers, data = read_csv("poster_chart_2_confidence.csv")
    if not data:
        return

    fig, ax = plt.subplots(figsize=(8, 5))

    # 清理标签："借用书籍文具类" → "书籍文具"，"有n笔交易" → "n"
    raw_labels = [r[0] for r in data]
    labels = []
    for lbl in raw_labels:
        lbl = lbl.replace("借用", "").replace("类", "")
        if "笔交易" in lbl:
            lbl = lbl.replace("有", "").replace("笔交易", "")
        labels.append(lbl)
    values = [float(r[1]) for r in data]

    bars = ax.bar(range(len(labels)), values, color=COLORS[0], width=0.6,
                  edgecolor="white", linewidth=1.5)

    # 在柱子上标数值
    for i, (bar, v) in enumerate(zip(bars, values)):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"{v:.0%}", ha="center", va="bottom", fontsize=11)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Confidence")
    ax.set_title("Cold-Start Confidence:\nPure Prior vs. Category Transfer vs. Own Data")
    ax.set_ylim(0, 1.15)
    ax.axhline(y=0.23, linestyle=":", color="gray", alpha=0.6, label="Pure Prior baseline")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.3)
    # 注释
    fig.text(0.5, -0.02,
             "Note: '书籍文具' means borrowing data from that category.\n"
             "Numbers like '1', '2' mean the item has that many transaction records.",
             ha="center", fontsize=10, color="gray")

    path = os.path.join(OUT_DIR, "fig2_cold_start.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════
# 图3：各分类价格分布
# ═══════════════════════════════════════════════════════════

def plot_category_prices():
    """分类价格分布柱状图（均价 + 区间）"""
    headers, data = read_csv("poster_chart_3_categories.csv")
    if not data:
        return

    # 过滤出汇总行（有"商品数"列的行）— 在 CSV 中位于第二部分
    # 找出 "分类" header 之后的数据
    summary_data = []
    for row in data:
        if len(row) >= 7 and row[1].replace(".", "").isdigit():
            summary_data.append(row)

    if not summary_data:
        # fallback: 用第一条汇总
        print("  ⚠️  未找到分类汇总数据")
        return

    fig, ax = plt.subplots(figsize=(9, 5))

    categories = [r[0] for r in summary_data]
    avg_prices = [float(r[3]) for r in summary_data]
    min_prices = [float(r[4]) for r in summary_data]
    max_prices = [float(r[5]) for r in summary_data]

    x = range(len(categories))
    colors = [COLOR_MAP.get(c, COLORS[-1]) for c in categories]

    bars = ax.bar(x, avg_prices, color=colors, width=0.6, edgecolor="white", linewidth=1.5)

    # 显示价格区间（min-max）作为误差线
    for i, (mn, mx) in enumerate(zip(min_prices, max_prices)):
        ax.plot([i, i], [mn, mx], color="black", linewidth=1.5, marker="_",
                markersize=8, markeredgewidth=1.5)

    # 标均价
    for bar, v in zip(bars, avg_prices):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 3,
                f"¥{v:.0f}", ha="center", va="bottom", fontsize=11)

    ax.set_xticks(list(x))
    ax.set_xticklabels(categories, rotation=20, ha="right")
    ax.set_ylabel("Transaction Price (¥)")
    ax.set_title("Price Distribution by Category\n(Bar = Average, Line = Min–Max Range)")
    ax.grid(axis="y", alpha=0.3)

    path = os.path.join(OUT_DIR, "fig3_category_prices.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════
# 图4：接受概率 Sigmoid 曲线
# ═══════════════════════════════════════════════════════════

def plot_accept_probability():
    """接受概率 Sigmoid 曲线：数据量对决策坚定程度的影响"""
    headers, data = read_csv("poster_chart_4_accept_prob.csv")
    if not data:
        return

    fig, ax = plt.subplots(figsize=(8, 5))

    # 第一部分：理论 Sigmoid 曲线（k=0.04, 0.07, 0.10）
    theory_data = []
    real_data = []
    section = "theory"
    for row in data:
        if len(row) >= 2 and row[0] == "出价偏移(元)":
            continue
        if len(row) >= 2 and row[0] == "offer":
            section = "real"
            continue
        if not row[0].replace("-", "").isdigit():
            continue
        if section == "theory" and len(row) >= 4:
            theory_data.append([float(row[0]), float(row[1]), float(row[2]), float(row[3])])
        elif section == "real" and len(row) >= 4:
            real_data.append([float(row[0]), float(row[1]), float(row[2]), float(row[3])])

    if theory_data:
        offsets = [r[0] for r in theory_data]
        ax.plot(offsets, [r[1] for r in theory_data],
                linewidth=2, color=COLORS[0], label="k=0.04 (No data)")
        ax.plot(offsets, [r[2] for r in theory_data],
                linewidth=2, color=COLORS[1], label="k=0.07 (Some data)")
        ax.plot(offsets, [r[3] for r in theory_data],
                linewidth=2, color=COLORS[2], label="k=0.10 (Sufficient data)")

    ax.set_xlabel("Offer Deviation from expected_mean (¥)")
    ax.set_ylabel("P(Accept)")
    ax.set_title("Acceptance Probability: Sigmoid Function\n(k increases with more data → sharper decision)")
    ax.legend(frameon=False)
    ax.grid(alpha=0.3)
    ax.set_ylim(-0.05, 1.05)
    ax.axvline(x=0, linestyle=":", color="gray", alpha=0.5)
    ax.axhline(y=0.5, linestyle=":", color="gray", alpha=0.5)

    path = os.path.join(OUT_DIR, "fig4_accept_prob.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Poster 图表生成器")
    print("=" * 60)

    # 检查 matplotlib
    try:
        import matplotlib
    except ImportError:
        print("\n❌ 需要安装 matplotlib：")
        print("   pip install matplotlib")
        return

    # 检查 CSV 是否存在
    csv_dir = OUT_DIR
    required = ["poster_chart_1_learning.csv", "poster_chart_2_confidence.csv",
                "poster_chart_3_categories.csv", "poster_chart_4_accept_prob.csv"]
    missing = [f for f in required if not os.path.exists(os.path.join(csv_dir, f))]
    if missing:
        print(f"\n❌ 缺少 CSV 文件：{missing}")
        print("   请先运行：")
        print("   python generate_demo_data.py --force")
        print("   python poster_charts.py")
        return

    print()

    plot_learning_curve()
    plot_confidence_comparison()
    plot_category_prices()
    plot_accept_probability()

    print(f"\n✅ 4 张图表已保存到 {OUT_DIR}/")
    print(f"   可直接插入 Postrer!")


if __name__ == "__main__":
    main()
