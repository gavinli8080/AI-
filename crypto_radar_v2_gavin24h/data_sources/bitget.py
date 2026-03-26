"""
Crypto Radar V2 - Bitget 数据源 (V2.5.1)
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

BASE_URL = "https://api.bitget.com"


class BitgetSource(BaseDataSource):
    name = "bitget"
    source_type = SignalSourceType.CEX_SPOT

    def __init__(self, config: Optional[ExchangeConfig] = None, http: Optional[HttpClient] = None):
        super().__init__(config, http)
        self.base_url = BASE_URL

    async def fetch_active_symbols(self) -> list[ActiveSymbolInfo]:
        url = f"{self.base_url}/api/v2/spot/public/symbols"
        data = await self.http.get_json(url)
        if not data or data.get("code") != "00000":
            self.logger.error(f"Bitget symbols error: {data}")
            return []

        results = []
        for s in data.get("data", []):
            sym = s.get("symbol", "")
            base_coin = s.get("baseCoin", "")
            quote_coin = s.get("quoteCoin", "")
            if quote_coin != "USDT":
                continue
            status = s.get("status", "").lower()
            is_active = status == "online"
            results.append(ActiveSymbolInfo(
                symbol=self.normalize_symbol(base_coin),
                base_asset=base_coin, quote_asset="USDT", raw_symbol=sym,
                status="active" if is_active else "inactive",
                trading_enabled=is_active, market_type="spot", exchange=self.name,
                trade_url=f"https://www.bitget.com/spot/{sym}" if is_active else "",
            ))
        self.logger.info(f"Bitget symbols: {len(results)} USDT spot pairs")
        return results

    async def fetch_tickers(self) -> list[MarketData]:
        await self.ensure_cache_fresh()
        if not self._fail_close_check():
            return []

        url = f"{self.base_url}/api/v2/spot/market/tickers"
        data = await self.http.get_json(url)
        if not data or data.get("code") != "00000":
            self.logger.error(f"Bitget ticker error: {data}")
            return []

        results = []
        skipped = 0
        for item in data.get("data", []):
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = sym.replace("USDT", "")
            normalized = self.normalize_symbol(base)
            if not self.is_symbol_active(normalized):
                skipped += 1
                continue
            price = float(item.get("lastPr", 0) or item.get("close", 0))
            if price <= 0:
                continue
            high_24h = float(item.get("high24h", 0))
            low_24h = float(item.get("low24h", 0))
            vol_24h = float(item.get("baseVolume", 0))
            quote_vol = float(item.get("quoteVolume", 0))
            change_24h = float(item.get("change24h", 0))
            if -1 < change_24h < 1 and change_24h != 0:
                change_24h *= 100
            volatility = ((high_24h - low_24h) / low_24h * 100) if low_24h > 0 else 0.0

            md = MarketData(
                symbol=normalized, source=self.name, price=price, timestamp=time.time(),
                periods=MultiPeriodData(change_24h=change_24h, volume_24h=vol_24h, turnover_24h=quote_vol),
                liquidity=LiquidityData(), volatility_24h=volatility,
                trade_url=self.get_validated_trade_url(normalized),
            )
            self._apply_validation(md, sym)
            results.append(md)

        if skipped:
            self.logger.debug(f"Bitget: skipped {skipped} non-active")
        self.logger.info(f"Bitget: {len(results)} active USDT tickers")
        return results

    async def fetch_klines(self, symbol: str, interval: str, limit: int = 50) -> list[dict]:
        api_symbol = symbol.replace("/", "")
        interval_map = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day"}
        url = f"{self.base_url}/api/v2/spot/market/candles"
        params = {"symbol": api_symbol, "granularity": interval_map.get(interval, interval), "limit": str(limit)}
        data = await self.http.get_json(url, params=params)
        if not data or data.get("code") != "00000":
            return []
        klines = []
        for k in reversed(data.get("data", [])):
            if len(k) < 6:
                continue
            klines.append({
                "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                "quote_volume": float(k[6]) if len(k) > 6 else 0, "trades": 0,
            })
        return klines
