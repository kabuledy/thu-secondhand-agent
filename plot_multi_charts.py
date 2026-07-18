"""
Poster 多轮汇总图表
===================
读取 poster_multi_learning.csv（poster_multi_run.py 的输出），
生成带误差棒的学术图表。

用法：
  python poster_multi_run.py 10
  python plot_multi_charts.py

依赖：
  pip install matplotlib

输出（poster_data/）：
  multi_fig1_learning.png     — 学习效果对比（柱状图+误差棒）
  multi_fig2_cold_start.png   — 冷启动置信度（柱状图+误差棒）
  multi_fig3_accept_prob.png  — 接受概率曲线（Sigmoid）
"""

import os, csv, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT_DIR = os.path.join(os.path.dirname(__file__), "poster_data")
CSV_PATH = os.path.join(OUT_DIR, "poster_multi_learning.csv")

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

COLORS = ["#2E86AB", "#A23B72", "#F18F01", "#44BBA4"]


def parse_csv():
    """解析 poster_multi_learning.csv 的各个部分"""
    if not os.path.exists(CSV_PATH):
        print(f"❌ 找不到 {CSV_PATH}")
        print("   请先运行：python poster_multi_run.py 10")
        return None

    data = {"global": [], "learning": [], "cold_start": [], "accept_prob": []}
    section = None
    headers = None

    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            first = row[0]

            if first.startswith("=== 全局统计"):
                section = "global"
                continue
            elif first.startswith("=== 学习效果"):
                section = "learning"
                continue
            elif first.startswith("=== 冷启动"):
                section = "cold_start"
                continue
            elif first.startswith("=== 接受概率"):
                section = "accept_prob"
                continue
            elif first.startswith("=== "):
                section = None
                continue

            if section == "global":
                data["global"].append(row)
            elif section == "learning" and row[0] != "指标":
                data["learning"].append(row)
            elif section == "cold_start" and row[0] != "场景":
                data["cold_start"].append(row)
            elif section == "accept_prob" and row[0] != "出价偏移(元)":
                data["accept_prob"].append(row)

    return data


# ═══════════════════════════════════════════════════════════
# 图1：学习效果（柱状图 + 误差棒）
# ═══════════════════════════════════════════════════════════

def plot_learning(data):
    """学习效果：先验 vs 最终估值，带标准差"""
    learning = data.get("learning", [])
    if not learning or len(learning) < 4:
        print("  ⚠️  学习效果数据不足")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # ── 左图：估值变化 ──
    labels_est = ["Prior\n(No data)", "After\nLearning"]
    means_est = [float(learning[0][1]), float(learning[1][1])]
    stds_est = [float(learning[0][2]), float(learning[1][2])]

    bars = ax1.bar(labels_est, means_est, yerr=stds_est, capsize=8,
                   color=[COLORS[0], COLORS[1]], width=0.5,
                   edgecolor="white", linewidth=1.5)

    for bar, v in zip(bars, means_est):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                f"¥{v:.0f}", ha="center", fontsize=13)

    ax1.set_ylabel("Expected Price (¥)")
    ax1.set_title("Learning Effect:\nEstimated Price Convergence")
    ax1.grid(axis="y", alpha=0.3)

    # ── 右图：置信度变化 ──
    labels_conf = ["Prior\n(No data)", "After\nLearning"]
    means_conf = [0.23, float(learning[2][1])]
    stds_conf = [0, float(learning[2][2])]

    bars = ax2.bar(labels_conf, means_conf, yerr=stds_conf, capsize=8,
                   color=[COLORS[0], COLORS[1]], width=0.5,
                   edgecolor="white", linewidth=1.5)

    for bar, v in zip(bars, means_conf):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"{v:.0%}", ha="center", fontsize=13)

    ax2.set_ylabel("Confidence")
    ax2.set_title("Confidence Improvement\n(After Multi-Session Bargaining)")
    ax2.set_ylim(0, 1.1)
    ax2.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    path = os.path.join(OUT_DIR, "multi_fig1_learning.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════
# 图2：冷启动置信度（柱状图 + 误差棒）
# ═══════════════════════════════════════════════════════════

def plot_cold_start(data):
    """冷启动：各分类置信度对比，带标准差"""
    cold = data.get("cold_start", [])
    if not cold:
        print("  ⚠️  冷启动数据不足")
        return

    fig, ax = plt.subplots(figsize=(9, 5))

    # 整理数据（去掉"类"后缀，缩短标签）
    labels = []
    means = []
    stds = []
    for row in cold:
        label = row[0].replace("借用", "").replace("类", "")
        labels.append(label)
        means.append(float(row[1]))
        stds.append(float(row[2]))

    x = range(len(labels))
    colors = [COLORS[i % len(COLORS)] for i in range(len(labels))]

    bars = ax.bar(x, means, yerr=stds, capsize=8, color=colors, width=0.6,
                  edgecolor="white", linewidth=1.5)

    for bar, v in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"{v:.0%}", ha="center", fontsize=12)

    # 纯先验基准线
    ax.axhline(y=0.23, linestyle=":", color="gray", alpha=0.6, linewidth=2,
               label="Pure Prior (0.23)")

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Confidence")
    ax.set_title("Cold-Start Confidence by Category\n(With Category Knowledge Transfer)")
    fig.text(0.5, -0.02, "Cold start = a new item with zero transaction data. Category transfer = borrowing data from similar items.", ha="center", fontsize=10, color="gray")
    ax.set_ylim(0, 1.15)
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.3)

    path = os.path.join(OUT_DIR, "multi_fig2_cold_start.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════
# 图3：接受概率曲线
# ═══════════════════════════════════════════════════════════

def plot_accept_prob(data):
    """接受概率 Sigmoid 曲线"""
    prob_data = data.get("accept_prob", [])
    if not prob_data:
        print("  ⚠️  接受概率数据不足")
        return

    fig, ax = plt.subplots(figsize=(8, 5))

    offsets = []
    probs = []
    stds = []
    for row in prob_data:
        if not row[0].replace("-", "").replace(".", "").isdigit():
            continue
        offsets.append(float(row[0]))
        probs.append(float(row[1]))
        stds.append(float(row[2]) if len(row) > 2 and row[2] else 0)

    ax.plot(offsets, probs, linewidth=3, color=COLORS[0], label="Average P(Accept)")
    # 绘制置信区间（±1 SD）
    upper = [min(p + s, 1.0) for p, s in zip(probs, stds)]
    lower = [max(p - s, 0.0) for p, s in zip(probs, stds)]
    ax.fill_between(offsets, upper, lower, alpha=0.2, color=COLORS[0], label="±1 SD")
    if all(s == 0 for s in stds):
        ax.text(0.5, 0.95, "Note: only 1 experiment, no variance data",
                transform=ax.transAxes, ha="center", fontsize=10, color="gray")

    ax.set_xlabel("Offer Deviation from expected_mean (¥)")
    ax.set_ylabel("P(Accept)")
    ax.set_title("Acceptance Probability (Sigmoid)\nAveraged Across Multiple Experiments")
    ax.legend(frameon=False)
    ax.grid(alpha=0.3)
    ax.set_ylim(-0.05, 1.05)
    ax.axvline(x=0, linestyle=":", color="gray", alpha=0.5)
    ax.axhline(y=0.5, linestyle=":", color="gray", alpha=0.5)

    path = os.path.join(OUT_DIR, "multi_fig3_accept_prob.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Poster 多轮汇总图表生成器")
    print("=" * 60)

    try:
        import matplotlib
    except ImportError:
        print("\n❌ 需要安装 matplotlib：pip install matplotlib")
        return

    data = parse_csv()
    if not data:
        return

    print()

    plot_learning(data)
    plot_cold_start(data)
    plot_accept_prob(data)

    print(f"\n✅ 3 张图表已保存到 {OUT_DIR}/")
    print(f"   可直接插入 Postrer!")


if __name__ == "__main__":
    main()
