"""
Crypto Radar V2 - Filter 单元测试
覆盖: 纯脉冲检测、假突破检测、尾段过热、跨交易所去重
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from models import (
    MarketData, MultiPeriodData, LiquidityData, MarketValidation,
    Signal, ScoreBreakdown, ScoreGrade, TradingAdvice, ChaseRiskDetail,
    SignalPhase, SignalSourceType, MarketStatus, ExecutionLevel,
)
from filters.signal_filter import SignalFilter
from config.settings import Settings


def _sig(symbol="TEST/USDT", source="binance", price=1.0, score=60.0,
         phase=SignalPhase.STARTUP, **period_kwargs) -> Signal:
    """构造测试用 Signal"""
    p = MultiPeriodData(**period_kwargs)
    md = MarketData(symbol=symbol, source=source, price=price, periods=p,
                    liquidity=LiquidityData(), trade_url=f"https://example.com/{symbol}")
    md.validation = MarketValidation(
        signal_source_type=SignalSourceType.CEX_SPOT,
        market_status=MarketStatus.ACTIVE,
        symbol_validated=True, price_verified=True,
    )
    sig = Signal(symbol=symbol, source=source, price=price, market_data=md, phase=phase)
    sig.score = ScoreBreakdown(total_score=score, grade=ScoreGrade.B)
    sig.advice = TradingAdvice(
        execution_priority_score=score,
        is_suitable_for_24h_trade=True,
        execution_level=ExecutionLevel.MAIN_SIGNAL.value,
    )
    sig.chase_risk = ChaseRiskDetail()
    return sig


class TestPostFilterPulse(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.f = SignalFilter(self.settings)
        self.f.new_round(100)

    def test_pure_5m_pulse_rejected(self):
        """5m暴涨>5% + 1h<2% → 纯脉冲拒绝"""
        sig = _sig(score=65, change_5m=6.0, change_1h=1.5, change_4h=3.0,
                   change_15m=2.0, change_24h=5.0,
                   turnover_24h=2_000_000, turnover_1h=200_000,
                   volume_ratio_5m=3.0)
        ok, reason = self.f.post_filter(sig)
        self.assertFalse(ok)
        self.assertIn("纯5m脉冲", reason)

    def test_normal_5m_passes(self):
        """5m适度涨+1h配合 → 通过"""
        sig = _sig(score=65, change_5m=3.0, change_1h=5.0, change_4h=3.0,
                   change_15m=4.0, change_24h=8.0,
                   turnover_24h=2_000_000, turnover_1h=200_000,
                   volume_ratio_5m=3.0)
        ok, _ = self.f.post_filter(sig)
        self.assertTrue(ok)


class TestPostFilterFakeBreakout(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.f = SignalFilter(self.settings)
        self.f.new_round(100)

    def test_fake_breakout_rejected(self):
        """5m涨>3% + 4h为负 → 被拒绝(先命中4h方向检查或假突破检查)"""
        sig = _sig(score=60, change_5m=4.0, change_1h=3.0, change_4h=-2.0,
                   change_15m=3.0, change_24h=5.0,
                   turnover_24h=2_000_000, turnover_1h=200_000,
                   volume_ratio_5m=2.5)
        ok, reason = self.f.post_filter(sig)
        self.assertFalse(ok)
        # 可能被"4h不为正"或"假突破"规则拒绝,两者都是正确行为
        self.assertTrue("4h" in reason or "假突破" in reason, f"Unexpected reason: {reason}")


class TestPostFilterTailSurge(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict")
        self.f = SignalFilter(self.settings)
        self.f.new_round(100)

    def test_tail_surge_rejected(self):
        """24h涨>10% 但4h只涨1% → 尾段无延续"""
        sig = _sig(score=60, change_5m=2.0, change_1h=3.0, change_4h=1.5,
                   change_15m=2.5, change_24h=12.0,
                   turnover_24h=2_000_000, turnover_1h=200_000,
                   volume_ratio_5m=2.5)
        ok, reason = self.f.post_filter(sig)
        self.assertFalse(ok)
        self.assertIn("尾段", reason)

    def test_healthy_24h_passes(self):
        """24h涨12% + 4h涨8% → 结构健康,通过"""
        sig = _sig(score=60, change_5m=2.0, change_1h=4.0, change_4h=8.0,
                   change_15m=3.0, change_24h=12.0,
                   turnover_24h=2_000_000, turnover_1h=200_000,
                   volume_ratio_5m=2.5)
        ok, _ = self.f.post_filter(sig)
        self.assertTrue(ok)


class TestCrossExchangeDedup(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(scoring_mode="strict", cross_exchange_dedup_enabled=True)
        self.f = SignalFilter(self.settings)

    def test_same_symbol_keeps_best(self):
        """同币多交易所只保留评分最高的"""
        sig_bn = _sig("BTC/USDT", "binance", score=70, turnover_24h=5_000_000)
        sig_ok = _sig("BTC/USDT", "okx", score=65, turnover_24h=3_000_000)
        sig_bg = _sig("BTC/USDT", "bitget", score=60, turnover_24h=1_000_000)

        kept, demoted = self.f.cross_exchange_dedup([sig_bn, sig_ok, sig_bg])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].source, "binance")
        self.assertEqual(len(demoted), 2)

    def test_different_symbols_all_kept(self):
        """不同币不受影响"""
        sig_a = _sig("BTC/USDT", "binance", score=70)
        sig_b = _sig("ETH/USDT", "okx", score=65)

        kept, demoted = self.f.cross_exchange_dedup([sig_a, sig_b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(demoted), 0)

    def test_disabled(self):
        """关闭时不去重"""
        self.settings.cross_exchange_dedup_enabled = False
        f2 = SignalFilter(self.settings)
        sig_a = _sig("BTC/USDT", "binance", score=70)
        sig_b = _sig("BTC/USDT", "okx", score=65)
        kept, demoted = f2.cross_exchange_dedup([sig_a, sig_b])
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
