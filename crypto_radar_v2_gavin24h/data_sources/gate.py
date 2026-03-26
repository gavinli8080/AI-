"""
Crypto Radar V2 - Gate.io 数据源 (V2.5.1)
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

BASE_URL = "https://api.gateio.ws"


class GateSource(BaseDataSource):
    name = "gate"
    source_type = SignalSourceType.CEX_SPOT

    def __init__(self, config: Optional[ExchangeConfig] = None, http: Optional[HttpClient] = None):
        super().__init__(config, http)
        self.base_url = BASE_URL

    async def fetch_active_symbols(self) -> list[ActiveSymbolInfo]:
        url = f"{self.base_url}/api/v4/spot/currency_pairs"
        data = await self.http.get_json(url)
        if not data or not isinstance(data, list):
            self.logger.error("Gate.io currency_pairs error")
            return []

        results = []
        for pair in data:
            pair_id = pair.get("id", "")
            if not pair_id.endswith("_USDT"):
                continue
            base = pair.get("base", pair_id.replace("_USDT", ""))
            quote = pair.get("quote", "USDT")
            trade_status = pair.get("trade_status", "").lower()
            is_active = trade_status == "tradable"
            results.append(ActiveSymbolInfo(
                symbol=self.normalize_symbol(base),
                base_asset=base, quote_asset=quote, raw_symbol=pair_id,
                status="active" if is_active else "inactive",
                trading_enabled=is_active, market_type="spot", exchange=self.name,
                trade_url=f"https://www.gate.io/trade/{pair_id}" if is_active else "",
            ))
        self.logger.info(f"Gate.io currency_pairs: {len(results)} USDT spot pairs")
        return results

    async def fetch_tickers(self) -> list[MarketData]:
        await self.ensure_cache_fresh()
        if not self._fail_close_check():
            return []

        url = f"{self.base_url}/api/v4/spot/tickers"
        data = await self.http.get_json(url)
        if not data or not isinstance(data, list):
            self.logger.error("Gate.io ticker error")
            return []

        results = []
        skipped = 0
        for item in data:
            pair = item.get("currency_pair", "")
            if not pair.endswith("_USDT"):
                continue
            base = pair.replace("_USDT", "")
            normalized = self.normalize_symbol(base)
            if not self.is_symbol_active(normalized):
                skipped += 1
                continue
            price = float(item.get("last", 0))
            if price <= 0:
                continue
            change_pct = float(item.get("change_percentage", 0))
            high_24h = float(item.get("high_24h", 0))
            low_24h = float(item.get("low_24h", 0))
            base_vol = float(item.get("base_volume", 0))
            quote_vol = float(item.get("quote_volume", 0))
            volatility = ((high_24h - low_24h) / low_24h * 100) if low_24h > 0 else 0.0

            md = MarketData(
                symbol=normalized, source=self.name, price=price, timestamp=time.time(),
                periods=MultiPeriodData(change_24h=change_pct, volume_24h=base_vol, turnover_24h=quote_vol),
                liquidity=LiquidityData(), volatility_24h=volatility,
                trade_url=self.get_validated_trade_url(normalized),
            )
            self._apply_validation(md, pair)
            results.append(md)

        if skipped:
            self.logger.debug(f"Gate.io: skipped {skipped} non-active")
        self.logger.info(f"Gate.io: {len(results)} active USDT tickers")
        return results

    async def fetch_klines(self, symbol: str, interval: str, limit: int = 50) -> list[dict]:
        api_pair = symbol.replace("/", "_")
        interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
        url = f"{self.base_url}/api/v4/spot/candlesticks"
        params = {"currency_pair": api_pair, "interval": interval_map.get(interval, interval), "limit": str(limit)}
        data = await self.http.get_json(url, params=params)
        if not data or not isinstance(data, list):
            return []
        klines = []
        for k in data:
            if len(k) < 7:
                continue
            klines.append({
                "timestamp": int(k[0]) * 1000, "open": float(k[5]), "high": float(k[3]),
                "low": float(k[4]), "close": float(k[2]), "volume": float(k[6]),
                "quote_volume": float(k[1]), "trades": 0,
            })
        return klines
