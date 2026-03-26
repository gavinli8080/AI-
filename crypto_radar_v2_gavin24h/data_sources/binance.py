"""
Crypto Radar V2 - Binance 数据源 (V2.5.1)
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

BASE_URL = "https://api.binance.com"


class BinanceSource(BaseDataSource):
    name = "binance"
    source_type = SignalSourceType.CEX_SPOT

    def __init__(self, config: Optional[ExchangeConfig] = None, http: Optional[HttpClient] = None):
        super().__init__(config, http)
        self.base_url = BASE_URL

    async def fetch_active_symbols(self) -> list[ActiveSymbolInfo]:
        url = f"{self.base_url}/api/v3/exchangeInfo"
        data = await self.http.get_json(url)
        if not data or "symbols" not in data:
            self.logger.error("Binance exchangeInfo failed")
            return []

        results = []
        for s in data["symbols"]:
            if s.get("quoteAsset") != "USDT":
                continue
            if s.get("isSpotTradingAllowed") is not True:
                continue
            status = s.get("status", "").upper()
            base = s.get("baseAsset", "")
            raw = s.get("symbol", "")
            if any(x in base for x in ["UP", "DOWN", "BULL", "BEAR"]):
                continue
            is_active = status == "TRADING"
            results.append(ActiveSymbolInfo(
                symbol=self.normalize_symbol(base),
                base_asset=base, quote_asset="USDT", raw_symbol=raw,
                status="active" if is_active else "inactive",
                trading_enabled=is_active, market_type="spot", exchange=self.name,
                trade_url=f"https://www.binance.com/trade/{base}_USDT" if is_active else "",
            ))
        self.logger.info(f"Binance exchangeInfo: {len(results)} USDT spot pairs")
        return results

    async def fetch_tickers(self) -> list[MarketData]:
        await self.ensure_cache_fresh()
        if not self._fail_close_check():
            return []

        url = f"{self.base_url}/api/v3/ticker/24hr"
        data = await self.http.get_json(url)
        if not data:
            return []

        results = []
        skipped = 0
        for item in data:
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = sym.replace("USDT", "")
            if any(x in base for x in ["UP", "DOWN", "BULL", "BEAR"]):
                continue
            normalized = self.normalize_symbol(base)
            if not self.is_symbol_active(normalized):
                skipped += 1
                continue
            price = float(item.get("lastPrice", 0))
            if price <= 0:
                continue
            vol_24h = float(item.get("volume", 0))
            quote_vol_24h = float(item.get("quoteVolume", 0))
            change_24h = float(item.get("priceChangePercent", 0))
            high_24h = float(item.get("highPrice", 0))
            low_24h = float(item.get("lowPrice", 0))
            trades_24h = int(item.get("count", 0))
            volatility = ((high_24h - low_24h) / low_24h * 100) if low_24h > 0 else 0.0

            md = MarketData(
                symbol=normalized, source=self.name, price=price, timestamp=time.time(),
                periods=MultiPeriodData(
                    change_24h=change_24h, volume_24h=vol_24h,
                    turnover_24h=quote_vol_24h, trades_24h=trades_24h,
                ),
                liquidity=LiquidityData(), volatility_24h=volatility,
                trade_url=self.get_validated_trade_url(normalized),
            )
            self._apply_validation(md, sym)
            results.append(md)

        if skipped:
            self.logger.debug(f"Binance: skipped {skipped} non-active")
        self.logger.info(f"Binance: {len(results)} active USDT tickers")
        return results

    async def fetch_klines(self, symbol: str, interval: str, limit: int = 50) -> list[dict]:
        api_symbol = symbol.replace("/", "")
        url = f"{self.base_url}/api/v3/klines"
        params = {"symbol": api_symbol, "interval": interval, "limit": limit}
        data = await self.http.get_json(url, params=params)
        if not data:
            return []
        return [
            {"timestamp": k[0], "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
             "quote_volume": float(k[7]), "trades": int(k[8])}
            for k in data
        ]
