"""
Crypto Radar V2 - DexScreener 数据源 (V2.5)
source_type = DEX, validation 字段正确标记
"""
from __future__ import annotations
import time
from typing import Optional
from models import (
    MarketData, MultiPeriodData, LiquidityData, ChainData,
    ActiveSymbolInfo, SignalSourceType, MarketStatus, MarketValidation,
)
from data_sources.base import BaseDataSource
from utils.http import HttpClient
from config.settings import Settings

BASE_URL = "https://api.dexscreener.com"


class DexScreenerSource(BaseDataSource):
    name = "dexscreener"
    source_type = SignalSourceType.DEX

    def __init__(self, settings: Optional[Settings] = None, http: Optional[HttpClient] = None):
        super().__init__(None, http)
        self.settings = settings
        self.chains = settings.dex_chains if settings else ["solana", "base", "ethereum", "bsc"]
        self.min_liquidity = settings.dex_min_liquidity if settings else 50_000
        self.min_pool_age_hours = settings.dex_min_pool_age_hours if settings else 24
        self.min_txns_1h = settings.dex_min_txns_1h if settings else 50

    async def fetch_active_symbols(self) -> list[ActiveSymbolInfo]:
        """DEX 没有固定的 active symbol list，返回空列表"""
        return []

    async def load_market_cache(self) -> int:
        """DEX 不需要 market cache"""
        self.logger.info(f"DexScreener: no market cache needed (DEX source)")
        return 0

    async def fetch_tickers(self) -> list[MarketData]:
        all_results = []
        for chain in self.chains:
            try:
                pairs = await self._fetch_chain_pairs(chain)
                all_results.extend(pairs)
            except Exception as e:
                self.logger.warning(f"DexScreener {chain} error: {e}")
        self.logger.info(f"DexScreener: fetched {len(all_results)} pairs across {len(self.chains)} chains")
        return all_results

    async def _fetch_chain_pairs(self, chain: str) -> list[MarketData]:
        search_url = f"{BASE_URL}/latest/dex/search"
        search_data = await self.http.get_json(search_url, params={"q": f"chain:{chain}"})

        pairs_data = []
        if search_data and "pairs" in search_data:
            pairs_data = search_data["pairs"]

        if not pairs_data:
            # fallback: token boosts
            url2 = f"{BASE_URL}/token-boosts/top/v1"
            boost_data = await self.http.get_json(url2)
            if isinstance(boost_data, list):
                for token_info in boost_data[:30]:
                    chain_id = token_info.get("chainId", "")
                    if chain_id.lower() != chain.lower():
                        continue
                    addr = token_info.get("tokenAddress", "")
                    if addr:
                        token_pairs = await self._fetch_token_pairs(addr)
                        pairs_data.extend(token_pairs)

        results = []
        for pair in pairs_data:
            md = self._parse_pair(pair, chain)
            if md:
                results.append(md)
        return results

    async def _fetch_token_pairs(self, token_address: str) -> list[dict]:
        url = f"{BASE_URL}/latest/dex/tokens/{token_address}"
        data = await self.http.get_json(url)
        if data and "pairs" in data:
            return data["pairs"][:5]
        return []

    def _parse_pair(self, pair: dict, default_chain: str = "") -> Optional[MarketData]:
        try:
            chain_id = pair.get("chainId", default_chain).lower()
            dex_id = pair.get("dexId", "")
            base_token = pair.get("baseToken", {})
            base_symbol = base_token.get("symbol", "???")
            pair_address = pair.get("pairAddress", "")

            price = float(pair.get("priceUsd", 0) or 0)
            if price <= 0:
                return None

            price_change = pair.get("priceChange", {})
            change_5m = float(price_change.get("m5", 0) or 0)
            change_1h = float(price_change.get("h1", 0) or 0)
            change_6h = float(price_change.get("h6", 0) or 0)
            change_24h = float(price_change.get("h24", 0) or 0)

            volume = pair.get("volume", {})
            vol_5m = float(volume.get("m5", 0) or 0)
            vol_1h = float(volume.get("h1", 0) or 0)
            vol_24h = float(volume.get("h24", 0) or 0)

            txns = pair.get("txns", {})
            txns_5m = txns.get("m5", {})
            txns_1h = txns.get("h1", {})
            buys_5m = int(txns_5m.get("buys", 0) or 0)
            sells_5m = int(txns_5m.get("sells", 0) or 0)
            buys_1h = int(txns_1h.get("buys", 0) or 0)
            sells_1h = int(txns_1h.get("sells", 0) or 0)

            liq = pair.get("liquidity", {})
            liquidity_usd = float(liq.get("usd", 0) or 0)

            market_cap = float(pair.get("marketCap", 0) or 0)
            fdv = float(pair.get("fdv", 0) or 0)

            pair_created = pair.get("pairCreatedAt", 0)
            pool_age_hours = 0
            if pair_created:
                pool_age_hours = (time.time() * 1000 - pair_created) / (1000 * 3600)

            total_txns_1h = buys_1h + sells_1h
            buys_ratio = buys_1h / total_txns_1h if total_txns_1h > 0 else 1.0

            pair_url = pair.get("url", f"https://dexscreener.com/{chain_id}/{pair_address}")

            # 基础过滤
            if liquidity_usd < self.min_liquidity:
                return None
            if pool_age_hours < self.min_pool_age_hours:
                return None
            if total_txns_1h < self.min_txns_1h:
                return None

            risk_tags = []
            if pool_age_hours < 48:
                risk_tags.append("pool_young")
            if liquidity_usd < 100_000:
                risk_tags.append("low_liquidity")
            if buys_ratio > 0.85:
                risk_tags.append("buy_heavy_suspicious")
            if buys_ratio < 0.3:
                risk_tags.append("sell_dominant")
            if market_cap > 0 and vol_24h > market_cap * 3:
                risk_tags.append("vol_mcap_anomaly")

            chain_data = ChainData(
                pool_age_hours=pool_age_hours,
                liquidity_usd=liquidity_usd,
                market_cap=market_cap,
                fdv=fdv,
                txns_5m=buys_5m + sells_5m,
                txns_1h=total_txns_1h,
                buys_vs_sells_ratio=buys_ratio,
                contract_risk_tags=risk_tags,
                chain=chain_id,
                pair_url=pair_url,
            )

            volatility = abs(change_24h) * 1.2 if change_24h else 0

            source_str = f"dex:{chain_id}:{dex_id}"

            md = MarketData(
                symbol=f"{base_symbol}/USD",
                source=source_str,
                price=price,
                timestamp=time.time(),
                periods=MultiPeriodData(
                    change_5m=change_5m,
                    change_1h=change_1h,
                    change_4h=change_6h,
                    change_24h=change_24h,
                    turnover_5m=vol_5m,
                    turnover_1h=vol_1h,
                    turnover_24h=vol_24h,
                    trades_5m=buys_5m + sells_5m,
                    trades_1h=total_txns_1h,
                ),
                liquidity=LiquidityData(
                    volume_to_mcap_ratio=vol_24h / market_cap if market_cap > 0 else 0,
                ),
                chain_data=chain_data,
                market_cap=market_cap,
                volatility_24h=volatility,
                chain_tag=chain_id,
                trade_url=pair_url,
                # V2.5: DEX validation
                validation=MarketValidation(
                    market_status=MarketStatus.ACTIVE,
                    signal_source_type=SignalSourceType.DEX,
                    symbol_validated=True,  # DEX pairs are inherently "live" if returned
                    price_verified=True,    # DEX price is the pool price itself
                    market_id_raw=pair_address,
                    source_validation_reason="",
                ),
            )
            return md

        except Exception as e:
            self.logger.debug(f"Parse pair error: {e}")
            return None

    async def fetch_klines(self, symbol: str, interval: str, limit: int = 50) -> list[dict]:
        return []
