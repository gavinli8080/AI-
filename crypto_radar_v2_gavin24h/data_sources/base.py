"""
Crypto Radar V2 - 数据源抽象基类 (V2.5.1)
Fail-close: 如果 market cache 未加载成功，CEX source 不生成候选
"""
from __future__ import annotations
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional
from models import (
    MarketData, MultiPeriodData, ActiveSymbolInfo,
    MarketStatus, SignalSourceType, MarketValidation,
)
from utils.http import HttpClient
from config.settings import ExchangeConfig


class BaseDataSource(ABC):
    name: str = "base"
    source_type: SignalSourceType = SignalSourceType.CEX_SPOT

    def __init__(self, config: Optional[ExchangeConfig] = None, http: Optional[HttpClient] = None):
        self.config = config
        self.http = http or HttpClient()
        self.logger = logging.getLogger(f"radar.source.{self.name}")

        self._market_cache: dict[str, ActiveSymbolInfo] = {}
        self._cache_loaded_at: float = 0
        self._cache_ttl: float = 1800
        self._cache_load_success: bool = False  # 是否至少成功加载过一次

    # =================== Market Cache ===================

    async def load_market_cache(self) -> int:
        try:
            symbols = await self.fetch_active_symbols()
            if symbols:
                self._market_cache = {s.symbol: s for s in symbols}
                self._cache_loaded_at = time.time()
                self._cache_load_success = True
                self.logger.info(f"{self.name} active spot symbols: {len(self._market_cache)}")
                return len(self._market_cache)
            else:
                self._cache_load_success = False
                self.logger.warning(f"{self.name} fetch_active_symbols returned empty list")
                return 0
        except Exception as e:
            self._cache_load_success = False
            self.logger.error(f"{self.name} load_market_cache FAILED: {e}")
            return 0

    async def ensure_cache_fresh(self):
        if time.time() - self._cache_loaded_at > self._cache_ttl:
            await self.load_market_cache()

    @property
    def cache_available(self) -> bool:
        """cache 是否可用: 至少成功加载过 且 非空"""
        return self._cache_load_success and len(self._market_cache) > 0

    def is_symbol_active(self, symbol: str) -> bool:
        info = self._market_cache.get(symbol)
        if not info:
            return False
        return info.status == "active" and info.trading_enabled

    def get_symbol_info(self, symbol: str) -> Optional[ActiveSymbolInfo]:
        return self._market_cache.get(symbol)

    def get_validated_trade_url(self, symbol: str) -> str:
        info = self._market_cache.get(symbol)
        if info and info.status == "active" and info.trade_url:
            return info.trade_url
        return ""

    @property
    def cache_size(self) -> int:
        return len(self._market_cache)

    # =================== Abstract Methods ===================

    @abstractmethod
    async def fetch_active_symbols(self) -> list[ActiveSymbolInfo]:
        ...

    @abstractmethod
    async def fetch_tickers(self) -> list[MarketData]:
        ...

    @abstractmethod
    async def fetch_klines(self, symbol: str, interval: str, limit: int = 50) -> list[dict]:
        ...

    # =================== Fail-Close Guard ===================

    def _fail_close_check(self) -> bool:
        """CEX source 在 cache 不可用时必须拒绝生成候选。
        返回 True = 可以继续, False = 本轮跳过
        """
        if self.source_type != SignalSourceType.CEX_SPOT:
            return True  # DEX 不需要 cache
        if not self.cache_available:
            self.logger.warning(
                f"{self.name}: market cache unavailable (loaded={self._cache_load_success}, "
                f"size={len(self._market_cache)}). SKIPPING this source for main candidates."
            )
            return False
        return True

    # =================== Price Validation ===================

    async def validate_price_consistency(
        self, md: MarketData, max_deviation_pct: float = 3.0
    ) -> MarketData:
        v = md.validation

        if md.price <= 0:
            v.price_verified = False
            v.source_validation_reason = "ticker价格<=0"
            return md

        try:
            klines = await self.fetch_klines(md.symbol, "1m", 2)
            if not klines:
                v.price_verified = False
                v.source_validation_reason = "无法获取K线数据"
                return md

            kline_close = klines[-1].get("close", 0)
            if kline_close <= 0:
                v.price_verified = False
                v.source_validation_reason = "K线close<=0"
                return md

            v.kline_close_price = kline_close
            deviation = abs(md.price - kline_close) / kline_close * 100
            v.price_deviation_pct = round(deviation, 2)

            if deviation <= max_deviation_pct:
                v.price_verified = True
            else:
                v.price_verified = False
                v.source_validation_reason = (
                    f"价格偏差{deviation:.1f}%>阈值{max_deviation_pct}% "
                    f"(ticker={md.price:.6g} kline={kline_close:.6g})"
                )
        except Exception as e:
            v.price_verified = False
            v.source_validation_reason = f"价格校验异常: {e}"

        return md

    # =================== Enrich ===================

    async def enrich_multi_period(self, md: MarketData) -> MarketData:
        try:
            klines_1m = await self.fetch_klines(md.symbol, "1m", 15)
            klines_5m = await self.fetch_klines(md.symbol, "5m", 15)
            klines_15m = await self.fetch_klines(md.symbol, "15m", 10)
            klines_1h = await self.fetch_klines(md.symbol, "1h", 10)
            klines_4h = await self.fetch_klines(md.symbol, "4h", 6)

            p = md.periods

            if klines_1m and len(klines_1m) >= 2:
                p.change_1m = _calc_change(klines_1m, 1)
                p.volume_1m = klines_1m[-1].get("volume", 0)

            if klines_5m and len(klines_5m) >= 2:
                p.change_5m = _calc_change(klines_5m, 1)
                p.volume_5m = sum(k.get("volume", 0) for k in klines_5m[-1:])
                if len(klines_5m) >= 6:
                    avg_vol = sum(k.get("volume", 0) for k in klines_5m[-6:-1]) / 5
                    p.volume_ratio_5m = p.volume_5m / avg_vol if avg_vol > 0 else 1.0

            if klines_15m and len(klines_15m) >= 2:
                p.change_15m = _calc_change(klines_15m, 1)
                p.volume_15m = sum(k.get("volume", 0) for k in klines_15m[-1:])
                if len(klines_15m) >= 5:
                    avg_vol = sum(k.get("volume", 0) for k in klines_15m[-5:-1]) / 4
                    p.volume_ratio_15m = p.volume_15m / avg_vol if avg_vol > 0 else 1.0

            if klines_1h and len(klines_1h) >= 2:
                p.change_1h = _calc_change(klines_1h, 1)
                p.volume_1h = sum(k.get("volume", 0) for k in klines_1h[-1:])
                if len(klines_1h) >= 5:
                    avg_vol = sum(k.get("volume", 0) for k in klines_1h[-5:-1]) / 4
                    p.volume_ratio_1h = p.volume_1h / avg_vol if avg_vol > 0 else 1.0

            if klines_4h and len(klines_4h) >= 2:
                p.change_4h = _calc_change(klines_4h, 1)

            price = md.price
            if price > 0:
                p.turnover_5m = p.volume_5m * price
                p.turnover_15m = p.volume_15m * price
                p.turnover_1h = p.volume_1h * price

            if klines_5m:
                p.trades_5m = klines_5m[-1].get("trades", 0)
            if klines_1h:
                p.trades_1h = klines_1h[-1].get("trades", 0)

        except Exception as e:
            self.logger.warning(f"enrich_multi_period failed for {md.symbol}: {e}")

        return md

    # =================== Helpers ===================

    def normalize_symbol(self, base: str, quote: str = "USDT") -> str:
        return f"{base.upper().strip()}/{quote.upper().strip()}"

    def _apply_validation(self, md: MarketData, raw_symbol: str) -> MarketData:
        v = md.validation
        v.signal_source_type = self.source_type
        v.market_id_raw = raw_symbol

        info = self._market_cache.get(md.symbol)
        if info:
            v.symbol_validated = True
            v.market_status = MarketStatus.ACTIVE if info.trading_enabled else MarketStatus.INACTIVE
            md.trade_url = info.trade_url
        elif self._market_cache:
            v.symbol_validated = False
            v.market_status = MarketStatus.UNKNOWN
            v.source_validation_reason = "不在active spot whitelist中"
            md.trade_url = ""
        else:
            v.symbol_validated = False
            v.market_status = MarketStatus.UNKNOWN
            v.source_validation_reason = "market cache未加载"

        return md

    async def close(self):
        await self.http.close()


def _calc_change(klines: list[dict], periods: int = 1) -> float:
    if len(klines) < periods + 1:
        return 0.0
    old_close = klines[-(periods + 1)].get("close", 0)
    new_close = klines[-1].get("close", 0)
    if old_close <= 0:
        return 0.0
    return ((new_close - old_close) / old_close) * 100
