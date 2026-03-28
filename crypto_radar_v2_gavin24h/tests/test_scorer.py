"""
Crypto Radar V2 - Scorer 单元测试
覆盖: 基本评分、phase判断、执行级别、24h分布结构惩罚
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from models import MarketData, MultiPeriodData, LiquidityData, MarketValidation, SignalPhase, ExecutionLevel, SignalSourceType, MarketStatus
from scoring.scorer import SignalScorer
from config.settings import Settings


def _md(**kwargs) -> MarketData:
    """构造测试用 MarketData"""
    periods = kwargs.pop("periods", {})
    p = MultiPeriodData(**periods)
    md = MarketData(
        symbol=kwargs.get("symbol", "TEST/USDT"),
        source=kwargs.get("source", "binance"),
        price=kwargs.get("price", 1.0),
        periods=p,
        liquidity=LiquidityData(),
        volatility_24h=kwargs.get("volatility_24h", 5.0),
    )
    md.validation = MarketValidation(
        signal_source_type=SignalSourceType.CEX_SPOT,
        market_status=MarketStatus.ACTIVE,
        symbol_validated=True,
        price_verified=True,
    )
    return md


class TestScorerBasic(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.scorer = SignalScorer(self.settings)

    def test_score_range(self):
        """评分应在0-100之间"""
        md = _md(periods={"change_5m": 3.0, "change_15m": 5.0, "change_1h": 8.0,
                          "change_4h": 5.0, "change_24h": 10.0,
                          "volume_ratio_5m": 3.0, "volume_ratio_15m": 2.0,
                          "turnover_24h": 2_000_000, "turnover_1h": 150_000})
        sig = self.scorer.score(md)
        self.assertGreaterEqual(sig.score.total_score, 0)
        self.assertLessEqual(sig.score.total_score, 100)

    def test_startup_phase(self):
        """适度涨幅+量比应进入启动阶段"""
        md = _md(periods={"change_5m": 2.0, "change_15m": 4.0, "change_1h": 8.0,
                          "change_4h": 5.0, "volume_ratio_5m": 2.5})
        sig = self.scorer.score(md)
        self.assertEqual(sig.phase, SignalPhase.STARTUP)

    def test_reject_extreme_1h(self):
        """1h暴涨应被判为REJECT"""
        md = _md(periods={"change_5m": 3.0, "change_15m": 10.0, "change_1h": 55.0,
                          "change_4h": 30.0, "volume_ratio_5m": 5.0})
        sig = self.scorer.score(md)
        self.assertEqual(sig.phase, SignalPhase.REJECT)

    def test_overheated_phase(self):
        """5m+15m同时大涨应判为过热"""
        md = _md(periods={"change_5m": 12.0, "change_15m": 18.0, "change_1h": 15.0,
                          "volume_ratio_5m": 4.0})
        sig = self.scorer.score(md)
        self.assertEqual(sig.phase, SignalPhase.OVERHEATED)


class TestExecutionLevel(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.scorer = SignalScorer(self.settings)

    def test_main_signal_level(self):
        """高分启动阶段应获得main_signal级别"""
        md = _md(periods={"change_5m": 2.5, "change_15m": 5.0, "change_1h": 6.0,
                          "change_4h": 4.0, "change_24h": 8.0,
                          "volume_ratio_5m": 3.5, "volume_ratio_15m": 2.5,
                          "turnover_24h": 5_000_000, "turnover_1h": 300_000,
                          "trades_5m": 100})
        sig = self.scorer.score(md)
        if sig.score.total_score >= self.settings.push_min_score and sig.phase == SignalPhase.STARTUP:
            self.assertEqual(sig.advice.execution_level, ExecutionLevel.MAIN_SIGNAL.value)

    def test_reject_level(self):
        """REJECT阶段应获得reject级别"""
        md = _md(periods={"change_5m": 8.0, "change_15m": 25.0, "change_1h": 55.0,
                          "change_4h": 40.0, "volume_ratio_5m": 8.0})
        sig = self.scorer.score(md)
        self.assertEqual(sig.advice.execution_level, ExecutionLevel.REJECT.value)


class TestTailSurgePenalty(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.scorer = SignalScorer(self.settings)

    def test_24h_early_concentration_penalty(self):
        """24h涨幅集中在早期(4h近乎0)应被额外惩罚"""
        # 24h涨12%但4h只涨1% → 涨幅集中在早期
        md_early = _md(periods={"change_5m": 2.0, "change_15m": 3.0, "change_1h": 4.0,
                                "change_4h": 1.0, "change_24h": 12.0,
                                "volume_ratio_5m": 2.5, "turnover_24h": 2_000_000})
        # 24h涨12%,4h涨8% → 涨幅分布均匀
        md_even = _md(periods={"change_5m": 2.0, "change_15m": 3.0, "change_1h": 4.0,
                               "change_4h": 8.0, "change_24h": 12.0,
                               "volume_ratio_5m": 2.5, "turnover_24h": 2_000_000})
        sig_early = self.scorer.score(md_early)
        sig_even = self.scorer.score(md_even)
        # 早期集中的应该被多扣分
        self.assertGreater(sig_early.score.overheat_penalty, sig_even.score.overheat_penalty)


class TestPositionFactor(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.scorer = SignalScorer(self.settings)

    def test_high_position_penalty(self):
        """24h区间顶部(>92%)应被惩罚"""
        md_high = _md(periods={"change_5m": 2.0, "change_15m": 3.0, "change_1h": 4.0,
                                "change_4h": 5.0, "change_24h": 10.0,
                                "volume_ratio_5m": 2.5, "turnover_24h": 2_000_000,
                                "position_in_24h_range": 0.95, "high_24h": 1.1, "low_24h": 0.9})
        md_low = _md(periods={"change_5m": 2.0, "change_15m": 3.0, "change_1h": 4.0,
                               "change_4h": 5.0, "change_24h": 10.0,
                               "volume_ratio_5m": 2.5, "turnover_24h": 2_000_000,
                               "position_in_24h_range": 0.3, "high_24h": 1.5, "low_24h": 0.8})
        sig_high = self.scorer.score(md_high)
        sig_low = self.scorer.score(md_low)
        self.assertGreater(sig_high.score.overheat_penalty, sig_low.score.overheat_penalty)

    def test_low_position_bonus(self):
        """低位启动(<40%)应在延续性获得加分"""
        md_low = _md(periods={"change_5m": 2.0, "change_15m": 3.0, "change_1h": 4.0,
                               "change_4h": 5.0, "change_24h": 10.0,
                               "volume_ratio_5m": 2.5, "volume_ratio_15m": 1.5,
                               "turnover_24h": 2_000_000,
                               "position_in_24h_range": 0.25})
        md_mid = _md(periods={"change_5m": 2.0, "change_15m": 3.0, "change_1h": 4.0,
                               "change_4h": 5.0, "change_24h": 10.0,
                               "volume_ratio_5m": 2.5, "volume_ratio_15m": 1.5,
                               "turnover_24h": 2_000_000,
                               "position_in_24h_range": 0.6})
        sig_low = self.scorer.score(md_low)
        sig_mid = self.scorer.score(md_mid)
        self.assertGreaterEqual(sig_low.score.continuation_score, sig_mid.score.continuation_score)


class TestWeakRepairPenalty(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.scorer = SignalScorer(self.settings)

    def test_weak_repair_penalized(self):
        """24h跌+1h微涨+量比不足 → 弱修复惩罚"""
        md = _md(periods={"change_5m": 1.0, "change_15m": 1.5, "change_1h": 1.5,
                          "change_4h": 1.0, "change_24h": -5.0,
                          "volume_ratio_5m": 1.2, "turnover_24h": 2_000_000})
        sig = self.scorer.score(md)
        penalties = sig.score.penalty_reasons
        self.assertTrue(any("弱修复" in r for r in penalties))


class TestVolRatioInconsistency(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.scorer = SignalScorer(self.settings)

    def test_inconsistent_vol_ratio_penalized(self):
        """5m量比>5但15m<1.5 → 量比不一致惩罚"""
        md = _md(periods={"change_5m": 3.0, "change_15m": 4.0, "change_1h": 6.0,
                          "change_4h": 5.0, "change_24h": 10.0,
                          "volume_ratio_5m": 8.0, "volume_ratio_15m": 1.2,
                          "turnover_24h": 2_000_000})
        sig = self.scorer.score(md)
        penalties = sig.score.penalty_reasons
        self.assertTrue(any("量比不一致" in r for r in penalties))


if __name__ == "__main__":
    unittest.main()
