"""
Crypto Radar V2 - OKX 数据源 (V2.5.1)
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

BASE_URL = "https://www.okx.com"


class OKXSource(BaseDataSource):
    name = "okx"
    source_type = SignalSourceType.CEX_SPOT

    def __init__(self, config: Optional[ExchangeConfig] = None, http: Optional[HttpClient] = None):
        super().__init__(config, http)
        self.base_url = BASE_URL

    async def fetch_active_symbols(self) -> list[ActiveSymbolInfo]:
        url = f"{self.base_url}/api/v5/public/instruments"
        params = {"instType": "SPOT"}
        data = await self.http.get_json(url, params=params)
        if not data or data.get("code") != "0":
            self.logger.error(f"OKX instruments error: {data}")
            return []

        results = []
        for inst in data.get("data", []):
            inst_id = inst.get("instId", "")
            if not inst_id.endswith("-USDT"):
                continue
            base = inst_id.replace("-USDT", "")
            state = inst.get("state", "").lower()
            is_active = state == "live"
            results.append(ActiveSymbolInfo(
                symbol=self.normalize_symbol(base),
                base_asset=base, quote_asset="USDT", raw_symbol=inst_id,
                status="active" if is_active else "inactive",
                trading_enabled=is_active, market_type="spot", exchange=self.name,
                trade_url=f"https://www.okx.com/trade-spot/{base.lower()}-usdt" if is_active else "",
            ))
        self.logger.info(f"OKX instruments: {len(results)} USDT spot pairs")
        return results

    async def fetch_tickers(self) -> list[MarketData]:
        await self.ensure_cache_fresh()
        if not self._fail_close_check():
            return []

        url = f"{self.base_url}/api/v5/market/tickers"
        params = {"instType": "SPOT"}
        data = await self.http.get_json(url, params=params)
        if not data or data.get("code") != "0":
            self.logger.error(f"OKX ticker error: {data}")
            return []

        results = []
        skipped = 0
        for item in data.get("data", []):
            inst_id = item.get("instId", "")
            if not inst_id.endswith("-USDT"):
                continue
            base = inst_id.replace("-USDT", "")
            normalized = self.normalize_symbol(base)
            if not self.is_symbol_active(normalized):
                skipped += 1
                continue
            price = float(item.get("last", 0))
            if price <= 0:
                continue
            open_24h = float(item.get("open24h", 0))
            vol_24h = float(item.get("vol24h", 0))
            vol_ccy_24h = float(item.get("volCcy24h", 0))
            high_24h = float(item.get("high24h", 0))
            low_24h = float(item.get("low24h", 0))
            change_24h = ((price - open_24h) / open_24h * 100) if open_24h > 0 else 0.0
            volatility = ((high_24h - low_24h) / low_24h * 100) if low_24h > 0 else 0.0

            md = MarketData(
                symbol=normalized, source=self.name, price=price, timestamp=time.time(),
                periods=MultiPeriodData(change_24h=change_24h, volume_24h=vol_24h, turnover_24h=vol_ccy_24h),
                liquidity=LiquidityData(), volatility_24h=volatility,
                trade_url=self.get_validated_trade_url(normalized),
            )
            self._apply_validation(md, inst_id)
            results.append(md)

        if skipped:
            self.logger.debug(f"OKX: skipped {skipped} non-active")
        self.logger.info(f"OKX: {len(results)} active USDT tickers")
        return results

    async def fetch_klines(self, symbol: str, interval: str, limit: int = 50) -> list[dict]:
        api_symbol = symbol.replace("/", "-")
        interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
        url = f"{self.base_url}/api/v5/market/candles"
        params = {"instId": api_symbol, "bar": interval_map.get(interval, interval), "limit": str(limit)}
        data = await self.http.get_json(url, params=params)
        if not data or data.get("code") != "0":
            return []
        klines = []
        for k in reversed(data.get("data", [])):
            klines.append({
                "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                "quote_volume": float(k[6]) if len(k) > 6 else 0, "trades": 0,
            })
        return klines
