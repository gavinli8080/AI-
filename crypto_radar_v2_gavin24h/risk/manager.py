"""
Crypto Radar V2 - 风控管理器
刷量检测、流动性质量评估、推送层风控辅助
"""
from __future__ import annotations
import logging
from models import MarketData, LiquidityData
from config.settings import Settings

logger = logging.getLogger("radar.risk")


class RiskManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def assess_liquidity(self, md: MarketData) -> MarketData:
        """评估流动性质量并标记刷量嫌疑"""
        p = md.periods
        liq = md.liquidity

        # === 刷量检测 ===
        suspected = False

        # 1. 成交额/市值异常高
        if md.market_cap > 0 and p.turnover_24h > md.market_cap * 5:
            suspected = True
            logger.debug(f"{md.symbol}: turnover/mcap={p.turnover_24h/md.market_cap:.1f}x")

        # 2. 成交笔数极少但量极大(大单刷)
        if p.trades_1h > 0 and p.turnover_1h > 0:
            avg_trade_size = p.turnover_1h / p.trades_1h
            if avg_trade_size > 50_000 and p.trades_1h < 20:
                suspected = True
                logger.debug(f"{md.symbol}: avg trade ${avg_trade_size:.0f} with only {p.trades_1h} trades")

        # 3. 量比异常高(>15x) 但价格几乎不动
        if p.volume_ratio_5m > 15 and abs(p.change_5m) < 0.5:
            suspected = True

        # 4. 链上: 买卖极度不均衡
        if md.chain_data:
            cd = md.chain_data
            if cd.buys_vs_sells_ratio > 0.9 or cd.buys_vs_sells_ratio < 0.1:
                suspected = True

        liq.suspected_wash_trading = suspected

        # === 深度评分(简化版,无需orderbook) ===
        depth_score = 50.0  # 默认中等
        if p.turnover_24h > 10_000_000:
            depth_score = 90
        elif p.turnover_24h > 5_000_000:
            depth_score = 75
        elif p.turnover_24h > 1_000_000:
            depth_score = 60
        elif p.turnover_24h > 500_000:
            depth_score = 45
        elif p.turnover_24h > 100_000:
            depth_score = 30
        else:
            depth_score = 15

        if suspected:
            depth_score *= 0.5

        liq.depth_score = depth_score

        # === 成交额/市值比 ===
        if md.market_cap > 0:
            liq.volume_to_mcap_ratio = p.turnover_24h / md.market_cap

        md.liquidity = liq
        return md

    def classify_style(self, md: MarketData) -> MarketData:
        """根据市值分类风格标签"""
        mcap = md.market_cap
        if mcap > 1_000_000_000:
            md.style_tag = "bluechip"
        elif mcap > 100_000_000:
            md.style_tag = "midcap"
        elif mcap > 10_000_000:
            md.style_tag = "smallcap"
        elif mcap > 0:
            md.style_tag = "micro"
        return md
