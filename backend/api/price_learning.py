"""
价格在线学习算法 — 用模拟+真实数据实时优化议价建议

核心思想（贝叶斯启发式在线学习）：
─────────────────────────────────────
不是深度学习（不需要 GPU、不需要训练），而是轻量级的在线更新：

  先验分布（卖家底价~标价之间的均匀分布）
      ↓ 每来一条新数据
  后验分布更新（贝叶斯更新）
      ↓
  给出建议价格 + 置信度

算法组成：
  1. 先验设定：以卖家底价和标价作为价格区间的边界
  2. 数据融合：模拟成交价 + 真实成交价 + 未成交记录的隐含下界
  3. 指数加权移动平均（EWMA）：越近的数据权重越大
  4. 置信度计算：数据量和离散度综合决定
  5. 接受概率模型：给定一个出价，估算卖家接受的概率

为什么不用传统机器学习？
  - 数据量小（每个商品可能只有3-10条记录）
  - 需要实时更新（每次议价后立即生效）
  - 不需要复杂的特征工程
  - 需要在 Flask 中轻量运行

用法：
  learner = PriceLearner(asking_price=300, seller_min_price=210)
  learner.add_sim_deal(250)
  learner.add_sim_deal(245)
  learner.add_sim_fail(230, 260)  # 买家出价230被拒
  learner.add_real_deal(255)

  suggestion = learner.suggest()
  # → {"starting_offer": 230, "expected_price": 248, "confidence": 0.72}

  prob = learner.acceptance_probability(240)
  # → 0.65 (65% 的概率卖家会接受240元的出价)
"""

from typing import List, Optional, Dict, Any
import math


class PriceLearner:
    """
    单个商品的在线价格学习器。
    每个商品实例化一个对象，从 bargain_data 恢复数据后使用。
    """

    def __init__(self, asking_price: float, seller_min_price: float,
                 condition_score: Optional[float] = None,
                 vision_confidence: Optional[float] = None):
        """
        初始化学习器。

        参数：
          asking_price:     标价（卖家挂出的价格）
          seller_min_price: 卖家底价（卖家愿意接受的最低价格）
          condition_score:  物理成色评分 0-100（来自视觉分析），None=未知
          vision_confidence: 视觉判断的置信度 0-1，None=未知
        """
        self.asking_price = asking_price
        self.seller_min_price = seller_min_price

        # ── 先验知识 ──
        # 在没有数据时，合理价格在 [min, asking] 的中间偏上
        self.prior_mean = (asking_price + seller_min_price) / 2
        self.prior_range = asking_price - seller_min_price

        # ── 数据存储 ──
        self.sim_prices: List[float] = []        # 模拟成交价
        self.sim_fails: List[dict] = []          # 模拟失败记录
        self.real_prices: List[float] = []       # 真实成交价

        # ── 参数 ──
        self.ewma_alpha = 0.35   # EWMA 衰减因子（越大越看重近期数据）
        self.real_weight = 2.0   # 真实数据的权重倍率（真实数据比模拟数据重要）

        # ── 🔬 物理成色参数（新增）──
        # 从视觉分析中获得，影响估值和置信度
        self.condition_score = condition_score     # 0-100, None=未知
        self.vision_confidence = vision_confidence # 0-1, None=未知

        # ── 衍生状态 ──
        self._cached_suggestion = None
        self._dirty = True       # 数据变化后置脏，延迟重新计算

    # ═══════════════════════════════════════════════════════
    # 数据输入接口
    # ═══════════════════════════════════════════════════════

    def add_sim_deal(self, price: float):
        """加入一条模拟成交数据"""
        self.sim_prices.append(price)
        self._dirty = True

    def add_sim_fail(self, buyer_offer: float, seller_counter: float,
                     reason: str = "buyer_declined"):
        """
        加入一条模拟失败记录。

        失败记录提供了"价格边界"信息：
        - buyer_declined: 买家出价 buyer_offer 但最后放弃了
           → 说明 buyer_offer 可能低于买家的预期
        - seller_rejected: 卖家拒绝了 buyer_offer
           → 说明 seller_min_price > buyer_offer
        """
        self.sim_fails.append({
            "buyer_offer": buyer_offer,
            "seller_counter": seller_counter,
            "reason": reason,
        })
        self._dirty = True

    def add_real_deal(self, price: float):
        """加入一条真实成交数据（权重更高）"""
        self.real_prices.append(price)
        self._dirty = True

    def bulk_load(self, sim_prices: List[float], sim_fails: List[dict],
                  real_prices: List[float]):
        """批量加载数据（从数据库恢复时用）"""
        self.sim_prices = list(sim_prices)
        self.sim_fails = list(sim_fails)
        self.real_prices = list(real_prices)
        self._dirty = True

    # ═══════════════════════════════════════════════════════
    # 核心算法：EWMA + 贝叶斯融合
    # ═══════════════════════════════════════════════════════

    def _compute_ewma(self, prices: List[float], alpha: float = None) -> Optional[float]:
        """计算指数加权移动平均"""
        if not prices:
            return None
        alpha = alpha or self.ewma_alpha
        ewma = prices[0]
        for p in prices[1:]:
            ewma = alpha * p + (1 - alpha) * ewma
        return ewma

    def _rebuild(self):
        """数据变化后重新计算所有衍生指标"""
        if not self._dirty:
            return
        self._dirty = False

        # ── 1. 构建有序价格序列用于 EWMA ──
        # 真实成交价每条重复两次，实现 real_weight = 2.0 的效果
        real_seq = []
        for p in self.real_prices:
            real_seq.append(p)
            real_seq.append(p)

        prices_seq = list(self.sim_prices) + real_seq

        # ── 2. EWMA 计算 ──
        # 以先验均值（底价与标价中点）作为 EWMA 的初始值
        #   → 零数据时自然回退到底价标价的中点
        #   → 有数据时逐步被真实观测「拉走」
        #   → EWMA 的指数衰减特性让先验初始值随数据增多自动消退
        if prices_seq:
            ewma = self.prior_mean
            for p in prices_seq:
                ewma = self.ewma_alpha * p + (1 - self.ewma_alpha) * ewma
            market_mean = ewma
        else:
            market_mean = self.prior_mean

        # ── 3. 处理失败记录的隐含下界 ──
        # 如果买家出价 X 被卖家拒绝 → 卖家底价 > X
        # 我们用它来向上修正最低可接受价格
        implied_floor = self.seller_min_price
        for fail in self.sim_fails:
            if fail["reason"] in ("seller_rejected", "seller_held_firm"):
                # 卖家拒绝了买家的出价 → 底价高于这个出价
                implied_floor = max(implied_floor, fail["buyer_offer"])

        # ── 4. 最终价格估算 ──
        # 结合市场平均和隐含下界（下界作为硬约束）
        # buffer = 先验区间的 4%，至少 2 元（避免小商品 +5 太夸张）
        implied_buffer = max(2.0, self.prior_range * 0.04)
        self.estimated_mean = max(market_mean, implied_floor + implied_buffer)
        # 确保不超过标价
        self.estimated_mean = min(self.estimated_mean, self.asking_price)

        # ── 4.5 🔬 物理成色调整（新增）──
        # 视觉识别出的物品物理状况，乘到估值上
        # 基准成色 = 70（正常二手物品）
        # condition_score 0-100 → multiplier 0.85-1.15
        if self.condition_score is not None:
            # 线性映射：0→0.85, 50→1.00, 70→1.06, 100→1.15
            condition_multiplier = 0.85 + (self.condition_score / 100) * 0.30

            # 视觉置信度越低，调整幅度越小（保守处理）
            if self.vision_confidence is not None:
                # 如果视觉判断只有60%把握，调整幅度打"把握度"折
                # 但至少保留 50% 的调整（即使视觉没信心，评分方向还是有参考价值）
                discount = max(0.5, self.vision_confidence)
                condition_multiplier = 1.0 + (condition_multiplier - 1.0) * discount

            # 应用到估值上
            self.estimated_mean = self.estimated_mean * condition_multiplier

            # 确保仍不超过标价（物理成色好不应导致超过全新价）
            self.estimated_mean = min(self.estimated_mean, self.asking_price)

        # ── 5. 置信度计算 ──
        total_points = len(self.sim_prices) + len(self.real_prices) * 2
        prior_weight = max(1.0, 5.0 - total_points * 0.5)
        n = total_points + prior_weight * 0.3  # 有效样本数

        # 数据量越大越自信，最多到 0.95
        data_confidence = min(0.95, n / (n + 3))

        # 价格离散度：数据越集中越自信
        all_prices = self.sim_prices + self.real_prices
        if len(all_prices) >= 2:
            mean_p = sum(all_prices) / len(all_prices)
            variance = sum((p - mean_p) ** 2 for p in all_prices) / len(all_prices)
            std_dev = math.sqrt(variance)
            # 离散度惩罚：标准差每增加标价的10%，置信度降10%
            dispersion_penalty = min(0.5, std_dev / (self.prior_range * 0.5))
        else:
            dispersion_penalty = 0.3  # 数据不足时保守

        self.confidence = round(data_confidence * (1 - dispersion_penalty), 2)

        # ── 6. 建议起始出价 ──
        # 买家第一次出价应该低于估计均价，留出还价空间
        self.suggested_starting_offer = round(self.estimated_mean - self.prior_range * 0.12, 0)
        # 确保不低于底价
        self.suggested_starting_offer = max(self.suggested_starting_offer,
                                            self.seller_min_price * 0.85)

        self._cached_suggestion = {
            "starting_offer": self.suggested_starting_offer,
            "expected_price": round(self.estimated_mean, 0),
            "price_range": {
                "low": round(implied_floor, 0),
                "high": self.asking_price,
            },
            "confidence": self.confidence,
            "data_points": {
                "sim_deals": len(self.sim_prices),
                "sim_fails": len(self.sim_fails),
                "real_deals": len(self.real_prices),
            },
            # 🔬 物理成色信息（如果有）
            "physical_condition": {
                "condition_score": self.condition_score,
                "vision_confidence": self.vision_confidence,
                "condition_adjustment_applied": self.condition_score is not None,
            } if self.condition_score is not None else None,
        }

    # ═══════════════════════════════════════════════════════
    # 对外接口
    # ═══════════════════════════════════════════════════════

    def suggest(self) -> dict:
        """
        获取议价建议。

        返回：
        {
            "starting_offer": 230,     # 建议买家首次出价
            "expected_price": 248,     # 预测最终成交价
            "price_range": {"low": 220, "high": 300},  # 合理价格区间
            "confidence": 0.72,        # 置信度 0-1
            "data_points": {           # 数据来源统计
                "sim_deals": 3,
                "sim_fails": 1,
                "real_deals": 2
            }
        }
        """
        self._rebuild()
        return self._cached_suggestion

    def acceptance_probability(self, offer_price: float) -> float:
        """
        给定一个出价，估算卖家接受的概率（0~1）。

        使用 Sigmoid 型函数：
          P(accept | offer) = 1 / (1 + exp(-k * (offer - midpoint)))

        其中 midpoint 在 estimated_mean 附近，
        k 控制曲线的陡峭程度（数据越多越陡峭）。
        """
        self._rebuild()

        # 中点 = 估计均值
        midpoint = self.estimated_mean

        # 陡峭度：数据越多，曲线越陡（卖家越坚定）
        total_n = len(self.sim_prices) + len(self.real_prices) * 2
        k = 0.04 + min(0.06, total_n * 0.01)  # 0.04 ~ 0.10

        # 标准化出价（相对于价格区间的偏移）
        normalized_offer = offer_price - midpoint

        # Sigmoid
        prob = 1.0 / (1.0 + math.exp(-k * normalized_offer))

        # 硬约束：低于底价时概率低
        if offer_price < self.seller_min_price:
            prob *= 0.3

        return round(min(prob, 0.98), 3)

    def get_learning_summary(self) -> dict:
        """
        获取学习摘要（Poster 和数据分析用）。
        """
        self._rebuild()
        suggestion = self._cached_suggestion

        summary = {
            "item_context": {
                "asking_price": self.asking_price,
                "seller_min_price": self.seller_min_price,
            },
            "data": {
                "sim_deals": self.sim_prices,
                "sim_fails": self.sim_fails,
                "real_deals": self.real_prices,
            },
            "suggestion": suggestion,
            "accuracy_estimate": self._estimate_accuracy(),
        }
        return summary

    def _estimate_accuracy(self) -> dict:
        """估算算法准确度（仅在至少有一条真实数据时才有意义）"""
        if not self.real_prices or not self.sim_prices:
            return {"status": "insufficient_data", "note": "暂无足够数据评估准确度"}

        # 用模拟数据预测真实价格
        sim_avg = sum(self.sim_prices) / len(self.sim_prices)
        real_avg = sum(self.real_prices) / len(self.real_prices)

        # 模拟预测误差
        errors = [abs(p - real_avg) for p in self.sim_prices]
        mae = sum(errors) / len(errors) if errors else None

        # EWMA 预测误差
        ewma = self._compute_ewma(self.sim_prices)
        ewma_error = abs(ewma - real_avg) if ewma else None

        return {
            "status": "has_data",
            "sim_avg": round(sim_avg, 2),
            "real_avg": round(real_avg, 2),
            "sim_vs_real_gap": round(sim_avg - real_avg, 2),
            "mae_of_sim_predictions": round(mae, 2) if mae else None,
            "ewma_prediction": round(ewma, 2) if ewma else None,
            "ewma_error_vs_real": round(ewma_error, 2) if ewma_error else None,
        }


# ═══════════════════════════════════════════════════════════
# 跨商品分类学习器
# ═══════════════════════════════════════════════════════════

class CategoryPriceLearner:
    """
    分类学习器：聚合同一分类下所有商品的数据，
    为新商品提供先验知识（冷启动时特别有用）。

    用法：
      learner = CategoryPriceLearner("数码")
      # 内部自动从数据库加载所有数码类商品的数据
      suggestion = learner.suggest_for_new_item(
          asking_price=300, seller_min_price=210
      )
      # 返回融合了同类商品经验的价格建议
    """

    def __init__(self, category: str):
        self.category = category
        self.category_prices = []     # 同类所有商品的成交价
        self.category_items = 0       # 同类有多少商品有数据

        self._load_category_data()

    def _load_category_data(self):
        """从数据库加载同类商品数据"""
        try:
            from .bargain_data import get_items_by_category
            records = get_items_by_category(self.category)
        except Exception:
            records = []

        self.category_items = len(records)
        self.category_prices = []

        for rec in records:
            self.category_prices.extend(rec.get("sim_prices", []))
            self.category_prices.extend(rec.get("real_prices", []))

    def suggest_for_new_item(self, asking_price: float,
                              seller_min_price: float) -> dict:
        """
        为分类下的一个新商品给出价格建议。
        融合了同类历史数据和当前商品的定价。
        """
        if not self.category_prices:
            # 没有同类数据，回退到简单的区间估算
            mid = (asking_price + seller_min_price) / 2
            return {
                "has_category_data": False,
                "category_items": 0,
                "starting_offer": mid - (asking_price - seller_min_price) * 0.15,
                "expected_price": mid,
                "confidence": 0.23,
                "insight": f"{self.category}类暂无交易数据，基于标价和底价估算。"
            }

        # 计算同类商品的价格特征
        cat_avg = sum(self.category_prices) / len(self.category_prices)
        cat_min = min(self.category_prices)
        cat_max = max(self.category_prices)

        # 计算标价比率（同类成交价 ÷ 标价的平均比例）
        # 用于新商品的价格预测
        # 使用加权平均：同类历史 + 当前商品区间
        current_mid = (asking_price + seller_min_price) / 2
        blended = (cat_avg * 0.6 + current_mid * 0.4)

        # 数据量越多，置信度越高
        data_points = len(self.category_prices)
        confidence = min(0.85, 0.23 + data_points * 0.04)

        return {
            "has_category_data": True,
            "category_items": self.category_items,
            "category_avg_price": round(cat_avg, 2),
            "category_price_range": (round(cat_min, 2), round(cat_max, 2)),
            "starting_offer": round(blended - (asking_price - seller_min_price) * 0.12, 0),
            "expected_price": round(blended, 0),
            "confidence": round(confidence, 2),
            "insight": (
                f"{self.category}类已有 {self.category_items} 件商品的 "
                f"{data_points} 笔交易数据，平均成交价 ¥{cat_avg:.0f}。"
                f"结合当前标价 ¥{asking_price} 和底价 ¥{seller_min_price}，"
                f"预计成交价约 ¥{blended:.0f}。"
            )
        }


# ═══════════════════════════════════════════════════════════
# 学习器工厂（从数据库记录恢复）
# ═══════════════════════════════════════════════════════════

def learner_from_db_record(record: dict) -> PriceLearner:
    """
    从数据库的一条 bargain_data 记录恢复 PriceLearner。
    """
    learner = PriceLearner(
        asking_price=record["asking_price"],
        seller_min_price=record["seller_min_price"],
    )
    learner.bulk_load(
        sim_prices=record.get("sim_prices", []),
        sim_fails=record.get("sim_fails", []),
        real_prices=record.get("real_prices", []),
    )
    return learner


# ═══════════════════════════════════════════════════════════
# 全局学习报告（Poster 用）
# ═══════════════════════════════════════════════════════════

def _get_category_breakdown(all_records: List[dict]) -> dict:
    """按分类统计学习数据"""
    from collections import defaultdict
    cats = defaultdict(lambda: {"items": 0, "sims": 0, "reals": 0, "prices": []})
    for rec in all_records:
        c = rec.get("category", "未分类")
        cats[c]["items"] += 1
        cats[c]["sims"] += rec.get("sim_count", 0)
        cats[c]["reals"] += rec.get("real_count", 0)
        cats[c]["prices"].extend(rec.get("sim_prices", []))
        cats[c]["prices"].extend(rec.get("real_prices", []))

    result = {}
    for c, data in sorted(cats.items()):
        avg_p = (sum(data["prices"]) / len(data["prices"])
                 if data["prices"] else None)
        result[c] = {
            "商品数": data["items"],
            "模拟次数": data["sims"],
            "真实交易": data["reals"],
            "平均成交价": round(avg_p, 1) if avg_p else None,
        }
    return result


def generate_global_learning_report(all_records: List[dict]) -> dict:
    """
    从所有商品的 bargain_data 记录生成全局学习报告。
    用于 Poster 展示"AI 在线学习效果"。
    """
    if not all_records:
        return {"status": "no_data", "message": "暂无数据"}

    learners = []
    for rec in all_records:
        learners.append(learner_from_db_record(rec))

    # 全局统计
    total_items = len(learners)
    items_with_real = sum(1 for l in learners if l.real_prices)
    items_with_sim = sum(1 for l in learners if l.sim_prices)

    # 模拟 vs 真实对比
    sim_real_diffs = []
    for l in learners:
        if l.sim_prices and l.real_prices:
            sim_avg = sum(l.sim_prices) / len(l.sim_prices)
            real_avg = sum(l.real_prices) / len(l.real_prices)
            sim_real_diffs.append(abs(sim_avg - real_avg))

    avg_abs_error = (sum(sim_real_diffs) / len(sim_real_diffs)
                     if sim_real_diffs else None)

    # 置信度分布
    confidences = [l.suggest()["confidence"] for l in learners]
    avg_confidence = (sum(confidences) / len(confidences)
                      if confidences else 0)

    return {
        "status": "success",
        "report": {
            "total_items_analyzed": total_items,
            "items_with_sim_data": items_with_sim,
            "items_with_real_data": items_with_real,
            "items_with_both": len(sim_real_diffs),
            "avg_sim_vs_real_error": round(avg_abs_error, 2) if avg_abs_error else None,
            "avg_algorithm_confidence": round(avg_confidence, 3),
            "confidence_distribution": {
                "high_confidence (>0.8)": sum(1 for c in confidences if c > 0.8),
                "medium_confidence (0.5-0.8)": sum(1 for c in confidences if 0.5 <= c <= 0.8),
                "low_confidence (<0.5)": sum(1 for c in confidences if c < 0.5),
            },
            "category_breakdown": _get_category_breakdown(all_records),
            "insight": (
                f"基于 {total_items} 件商品的议价数据，"
                f"算法平均置信度 {avg_confidence:.0%}。"
                + (f"模拟预测与真实成交的平均误差约 {avg_abs_error:.0f} 元。"
                   if avg_abs_error else " 暂未有足够真实数据评估预测准确度。")
            )
        }
    }
