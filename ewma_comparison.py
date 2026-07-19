"""
EWMA vs 加权平均 — 收敛对比图（Poster 专用）
============================================
模拟一个"市场价突变"场景，对比旧加权平均算法和
新 EWMA 算法对价格趋势的追踪速度差异。

用法：
  python ewma_comparison.py

输出：
  poster_data/fig_ewma_comparison.png
"""

import os, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT_DIR = os.path.join(os.path.dirname(__file__), "poster_data")
os.makedirs(OUT_DIR, exist_ok=True)

# ── 图表风格（与 plot_charts.py 一致）──
plt.rcParams.update({
    "font.size": 14,
    "axes.titlesize": 18,
    "axes.labelsize": 15,
    "legend.fontsize": 12,
    "figure.facecolor": "white",
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
    "font.family": ["Microsoft YaHei", "SimHei", "sans-serif"],
})

# ── 场景参数 ──
ASKING_PRICE = 88
MIN_PRICE = 52
PRIOR_MEAN = (ASKING_PRICE + MIN_PRICE) / 2  # 70
PRIOR_RANGE = ASKING_PRICE - MIN_PRICE       # 36

# 模拟数据：前 5 笔淡季低价，后 5 笔旺季涨价
DATA_PHASE1 = [52, 54, 53, 52, 55]   # 淡季（~53 avg）
DATA_PHASE2 = [72, 75, 70, 78, 73]   # 旺季（~74 avg）
ALL_PRICES = DATA_PHASE1 + DATA_PHASE2
N = len(ALL_PRICES)


# ═══════════════════════════════════════════════════════════
# 旧算法：加权平均
# ═══════════════════════════════════════════════════════════
def old_weighted_estimate(seen: list) -> float:
    """累积加权平均（还原旧 price_learning.py 的 _rebuild 逻辑）"""
    # 这里为了对比清晰，将所有数据视为"模拟"权重 ×1.0
    # （真实情况 sim/real 分开，但对比图侧重看"加权平均 vs EWMA"的本质差异）
    weighted_sum = 0.0
    total_weight = 0.0
    for p in seen:
        weighted_sum += p * 1.0
        total_weight += 1.0

    prior_weight = max(1.0, 5.0 - total_weight * 0.5)
    weighted_sum += PRIOR_MEAN * prior_weight
    total_weight += prior_weight

    market_mean = weighted_sum / total_weight
    buffer = max(2.0, PRIOR_RANGE * 0.04)
    estimated_mean = max(market_mean, MIN_PRICE + buffer)
    estimated_mean = min(estimated_mean, ASKING_PRICE)
    return round(estimated_mean, 1)


# ═══════════════════════════════════════════════════════════
# 新算法：EWMA
# ═══════════════════════════════════════════════════════════
def ewma_estimate(seen: list, alpha: float = 0.35) -> float:
    """累积 EWMA（复现当前 price_learning.py 的 _rebuild 逻辑）"""
    if not seen:
        return PRIOR_MEAN
    ewma = PRIOR_MEAN
    for p in seen:
        ewma = alpha * p + (1 - alpha) * ewma
    return round(ewma, 1)


# ═══════════════════════════════════════════════════════════
# 逐条计算
# ═══════════════════════════════════════════════════════════
old_estimates = []
new_estimates = []

for i in range(1, N + 1):
    seen = ALL_PRICES[:i]
    old_estimates.append(old_weighted_estimate(seen))
    new_estimates.append(ewma_estimate(seen))


# ═══════════════════════════════════════════════════════════
# 绘图
# ═══════════════════════════════════════════════════════════
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8),
                                 gridspec_kw={"height_ratios": [3, 1]})
x = list(range(1, N + 1))

# ── 主图：估计值收敛曲线 ──
ax1.plot(x, old_estimates, "o-", color="#C73E1D", linewidth=2.2,
         markersize=6, label="加权平均 (旧)", alpha=0.85)
ax1.plot(x, new_estimates, "s-", color="#2E86AB", linewidth=2.5,
         markersize=6, label=f"EWMA α=0.35 (新)", alpha=0.9)

# 真实价格点（散点，不连线）
ax1.scatter(x, ALL_PRICES, color="#555555", s=30, zorder=5, alpha=0.6,
            label="单次成交价", marker="x")

# 分阶段参考线
phase1_avg = sum(DATA_PHASE1) / len(DATA_PHASE1)
phase2_avg = sum(DATA_PHASE2) / len(DATA_PHASE2)
ax1.axhline(phase1_avg, xmin=0, xmax=0.5, color="#888888", linestyle="--",
            linewidth=1, alpha=0.5)
ax1.axhline(phase2_avg, xmin=0.5, xmax=1, color="#888888", linestyle="--",
            linewidth=1, alpha=0.5)
ax1.text(1.5, phase1_avg + 1, f"淡季均 ¥{phase1_avg:.0f}", fontsize=10,
         color="#888888")
ax1.text(6.5, phase2_avg + 1, f"旺季均 ¥{phase2_avg:.0f}", fontsize=10,
         color="#888888")

# 市场突变分界线
ax1.axvline(x=5.5, color="#000000", linestyle=":", linewidth=1.2, alpha=0.4)
ax1.text(5.7, ax1.get_ylim()[1] * 0.95, "市场需求突变", fontsize=11,
         rotation=90, color="#666666", va="top")

ax1.set_xlabel("数据条数（按时间顺序）")
ax1.set_ylabel("预估成交价 (¥)")
ax1.set_title("EWMA vs 加权平均 — 价格预估收敛速度对比")
ax1.legend(loc="lower right", framealpha=0.9)
ax1.set_xlim(0.5, N + 0.5)
ax1.grid(True, alpha=0.2)
ax1.set_xticks(x)

# ── 副图：误差对比（偏离近期真实均值的距离） ──
# 每一点的"真实值" = 该点之后实际价的移动均值（用后3条）
def trailing_true(idx, window=3):
    """用当前点及之后 window-1 条的实际价作为"当前真实值"估计"""
    end = min(idx + window, N)
    return sum(ALL_PRICES[idx-1:end]) / (end - idx + 1)

true_values = [trailing_true(i, 3) for i in range(1, N + 1)]
old_errors = [abs(old_estimates[i] - true_values[i]) for i in range(N)]
new_errors = [abs(new_estimates[i] - true_values[i]) for i in range(N)]

ax2.bar([i - 0.15 for i in x], old_errors, width=0.3,
        color="#C73E1D", alpha=0.65, label="加权平均误差")
ax2.bar([i + 0.15 for i in x], new_errors, width=0.3,
        color="#2E86AB", alpha=0.65, label="EWMA 误差")
ax2.axvline(x=5.5, color="#000000", linestyle=":", linewidth=1.2, alpha=0.4)

ax2.set_xlabel("数据条数（按时间顺序）")
ax2.set_ylabel("与近期均价偏差 (¥)")
ax2.set_title("预估偏差对比（偏差越小 = 追踪越快）")
ax2.legend(loc="upper right", framealpha=0.9)
ax2.set_xlim(0.5, N + 0.5)
ax2.set_xticks(x)
ax2.grid(True, alpha=0.2)

plt.tight_layout()
out_path = os.path.join(OUT_DIR, "fig_ewma_comparison.png")
plt.savefig(out_path)
print(f"✅ 已生成：{out_path}")

# ── 终端输出数据对比 ──
print()
print(f"{'i':>3} {'实际价':>6} {'加权平均':>8} {'EWMA':>6} {'加权误差':>8} {'EWMA误差':>6}")
print("-" * 50)
for i in range(N):
    print(f"{i+1:>3} {ALL_PRICES[i]:>6.0f} {old_estimates[i]:>8.1f} {new_estimates[i]:>6.1f} "
          f"{old_errors[i]:>8.2f} {new_errors[i]:>6.2f}")

avg_old_err = sum(old_errors) / len(old_errors)
avg_new_err = sum(new_errors) / len(new_errors)
print("-" * 50)
print(f"    平均偏差     {avg_old_err:>8.2f} {avg_new_err:>6.2f}")
print(f"    EWMA 改进: {(1 - avg_new_err/avg_old_err)*100:.1f}%")
