"""
Crypto Radar V2 - 信号追踪器
持久化追踪信号后续表现，支持 15m/1h/4h/24h 多节点检查
重启后从数据库恢复未完成的追踪任务
"""
from __future__ import annotations
import time
import logging
import asyncio
from typing import Optional
from models import Signal, TrackingRecord
from storage.db import Database
from data_sources.base import BaseDataSource
from config.settings import Settings

logger = logging.getLogger("radar.tracker")


class SignalTracker:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._sources: dict[str, BaseDataSource] = {}

    def register_source(self, name: str, source: BaseDataSource):
        """注册数据源用于追踪时获取最新价格"""
        self._sources[name] = source

    def start_tracking(self, signal: Signal):
        """为新推送的信号创建追踪记录"""
        tr = TrackingRecord(
            signal_id=signal.id,
            symbol=signal.symbol,
            source=signal.source,
            entry_price=signal.price,
            entry_time=time.time(),
            phase_at_entry=signal.phase.value,
            grade_at_entry=signal.score.grade.value,
            score_at_entry=signal.score.total_score,
            high_price=signal.price,
            low_price=signal.price,
        )
        self.db.save_tracking(tr)
        logger.info(f"Started tracking {signal.symbol} @ {signal.price:.6g}")

    async def check_pending(self):
        """检查所有到期的追踪任务，更新价格"""
        pending = self.db.get_pending_tracking()
        if not pending:
            return

        logger.info(f"Checking {len(pending)} pending tracking tasks")

        for row in pending:
            try:
                await self._update_tracking(row)
            except Exception as e:
                logger.error(f"Tracking check error for {row['symbol']}: {e}")

    async def _update_tracking(self, row: dict):
        """更新单个追踪记录"""
        symbol = row["symbol"]
        source_name = row["source"]
        entry_price = row["entry_price"]
        check_stage = row["check_stage"]

        # 获取当前价格
        current_price = await self._get_current_price(symbol, source_name)
        if current_price is None or current_price <= 0:
            logger.warning(f"Cannot get price for {symbol}, skip this check")
            return

        # 构建 TrackingRecord
        tr = TrackingRecord(
            signal_id=row["signal_id"],
            symbol=symbol,
            source=source_name,
            entry_price=entry_price,
            entry_time=row["entry_time"],
            phase_at_entry=row["phase_at_entry"],
            grade_at_entry=row["grade_at_entry"],
            score_at_entry=row["score_at_entry"],
            price_15m=row["price_15m"],
            price_1h=row["price_1h"],
            price_4h=row["price_4h"],
            price_24h=row["price_24h"],
            high_price=max(row["high_price"] or entry_price, current_price),
            low_price=min(row["low_price"] or entry_price, current_price),
        )

        # 根据阶段更新
        if check_stage == "15m":
            tr.price_15m = current_price
        elif check_stage == "1h":
            tr.price_1h = current_price
        elif check_stage == "4h":
            tr.price_4h = current_price
        elif check_stage == "24h":
            tr.price_24h = current_price
            tr.is_completed = True
            tr.completed_time = time.time()

        # 计算收益指标
        if entry_price > 0:
            tr.max_gain_pct = ((tr.high_price - entry_price) / entry_price) * 100
            tr.max_drawdown_pct = ((tr.low_price - entry_price) / entry_price) * 100
            if tr.is_completed and tr.price_24h > 0:
                tr.result_24h_pct = ((tr.price_24h - entry_price) / entry_price) * 100

        self.db.save_tracking(tr)

        status = "COMPLETED" if tr.is_completed else f"stage={check_stage}"
        change_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        logger.info(
            f"Tracking [{status}] {symbol}: entry={entry_price:.6g} "
            f"now={current_price:.6g} ({change_pct:+.1f}%) "
            f"maxGain={tr.max_gain_pct:+.1f}% maxDD={tr.max_drawdown_pct:+.1f}%"
        )

    async def _get_current_price(self, symbol: str, source_name: str) -> Optional[float]:
        """从对应数据源获取最新价格"""
        # 提取基础source名: "dex:solana:raydium" -> "dexscreener"
        base_source = source_name.split(":")[0] if ":" in source_name else source_name
        if base_source == "dex":
            base_source = "dexscreener"

        source = self._sources.get(base_source)
        if not source:
            # 尝试任意可用源
            for s in self._sources.values():
                try:
                    klines = await s.fetch_klines(symbol, "1m", 1)
                    if klines:
                        return klines[-1].get("close", 0)
                except Exception:
                    continue
            return None

        try:
            klines = await source.fetch_klines(symbol, "1m", 1)
            if klines:
                return klines[-1].get("close", 0)
        except Exception:
            pass

        # fallback: 从 ticker 获取
        try:
            tickers = await source.fetch_tickers()
            for t in tickers:
                if t.symbol == symbol:
                    return t.price
        except Exception:
            pass

        return None

    def get_stats(self) -> dict:
        """获取追踪统计"""
        return self.db.get_performance_stats()
