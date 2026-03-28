"""
Crypto Radar V2 - Bybit 数据源 (V2.5.1)
"""
from __future__ import annotations
import time
from typing import Optional
from models import (
    MarketData, MultiPeriodData, LiquidityData,
    ActiveSymbolInfo, SignalSourceType,
)
from data_sources.base import BaseDataSource
from utils.http import HttpClient
from config.settings import ExchangeConfig

BASE_URL = "https://api.bybit.com"


class BybitSource(BaseDataSource):
    name = "bybit"
    source_type = SignalSourceType.CEX_SPOT

    def __init__(self, config: Optional[ExchangeConfig] = None, http: Optional[HttpClient] = None):
        super().__init__(config, http)
        self.base_url = BASE_URL

    async def fetch_active_symbols(self) -> list[ActiveSymbolInfo]:
        url = f"{self.base_url}/v5/market/instruments-info"
        params = {"category": "spot"}
        data = await self.http.get_json(url, params=params)
        if not data or data.get("retCode") != 0:
            self.logger.error(f"Bybit instruments error: {data}")
            return []

        results = []
        for inst in data.get("result", {}).get("list", []):
            sym = inst.get("symbol", "")
            base = inst.get("baseCoin", "")
            quote = inst.get("quoteCoin", "")
            if quote != "USDT":
                continue
            status = inst.get("status", "").lower()
            is_active = status == "trading"
            results.append(ActiveSymbolInfo(
                symbol=self.normalize_symbol(base),
                base_asset=base, quote_asset="USDT", raw_symbol=sym,
                status="active" if is_active else "inactive",
                trading_enabled=is_active, market_type="spot", exchange=self.name,
                trade_url=f"https://www.bybit.com/trade/spot/{base}/USDT" if is_active else "",
            ))
        self.logger.info(f"Bybit instruments: {len(results)} USDT spot pairs")
        return results

    async def fetch_tickers(self) -> list[MarketData]:
        await self.ensure_cache_fresh()
        if not self._fail_close_check():
            return []

        url = f"{self.base_url}/v5/market/tickers"
        params = {"category": "spot"}
        data = await self.http.get_json(url, params=params)
        if not data or data.get("retCode") != 0:
            self.logger.error(f"Bybit ticker error: {data}")
            return []

        results = []
        skipped = 0
        for item in data.get("result", {}).get("list", []):
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = sym.replace("USDT", "")
            normalized = self.normalize_symbol(base)
            if not self.is_symbol_active(normalized):
                skipped += 1
                continue
            price = float(item.get("lastPrice", 0))
            if price <= 0:
                continue
            prev_price = float(item.get("prevPrice24h", 0))
            change_24h = ((price - prev_price) / prev_price * 100) if prev_price > 0 else 0.0
            high_24h = float(item.get("highPrice24h", 0))
            low_24h = float(item.get("lowPrice24h", 0))
            vol_24h = float(item.get("volume24h", 0))
            turnover_24h = float(item.get("turnover24h", 0))
            volatility = ((high_24h - low_24h) / low_24h * 100) if low_24h > 0 else 0.0

            md = MarketData(
                symbol=normalized, source=self.name, price=price, timestamp=time.time(),
                periods=MultiPeriodData(change_24h=change_24h, volume_24h=vol_24h, turnover_24h=turnover_24h,
                    high_24h=high_24h, low_24h=low_24h,
                    position_in_24h_range=round(((price-low_24h)/(high_24h-low_24h)) if high_24h>low_24h>0 else 0.5, 3)),
                liquidity=LiquidityData(), volatility_24h=volatility,
                trade_url=self.get_validated_trade_url(normalized),
            )
            self._apply_validation(md, sym)
            results.append(md)

        if skipped:
            self.logger.debug(f"Bybit: skipped {skipped} non-active")
        self.logger.info(f"Bybit: {len(results)} active USDT tickers")
        return results

    async def fetch_klines(self, symbol: str, interval: str, limit: int = 50) -> list[dict]:
        api_symbol = symbol.replace("/", "")
        interval_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
        url = f"{self.base_url}/v5/market/kline"
        params = {"category": "spot", "symbol": api_symbol,
                  "interval": interval_map.get(interval, "5"), "limit": str(limit)}
        data = await self.http.get_json(url, params=params)
        if not data or data.get("retCode") != 0:
            return []
        klines = []
        for k in reversed(data.get("result", {}).get("list", [])):
            if len(k) < 6:
                continue
            klines.append({
                "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                "quote_volume": float(k[6]) if len(k) > 6 else 0, "trades": 0,
            })
        return klines
