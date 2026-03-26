"""
Crypto Radar V2 (V2.9)
严格双流隔离, fallback仅从合法观察池选, 多因子fallback排序
"""
from __future__ import annotations
import sys, os, asyncio, logging, time, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import load_settings, Settings
from models import MarketData, Signal, SignalSourceType, SignalPhase
from data_sources.binance import BinanceSource
from data_sources.okx import OKXSource
from data_sources.bitget import BitgetSource
from data_sources.gate import GateSource
from data_sources.bybit import BybitSource
from data_sources.dexscreener import DexScreenerSource
from data_sources.base import BaseDataSource
from utils.http import HttpClient
from scoring.scorer import SignalScorer
from filters.signal_filter import SignalFilter
from risk.manager import RiskManager
from notifications.telegram import TelegramNotifier
from tracking.tracker import SignalTracker
from storage.db import Database

def setup_logging(s: Settings):
    os.makedirs(os.path.dirname(s.log_file) or ".", exist_ok=True)
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(s.log_file, encoding="utf-8")])

logger = logging.getLogger("radar.main")


class CryptoRadar:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.scorer = SignalScorer(settings)
        self.filter = SignalFilter(settings)
        self.risk_mgr = RiskManager(settings)
        self.notifier = TelegramNotifier(settings)
        self.tracker = SignalTracker(settings, self.db)
        self.sources: list[BaseDataSource] = []
        self.source_map: dict[str, BaseDataSource] = {}
        self._init_sources()

    def _init_sources(self):
        s = self.settings
        for cfg, name, cls in [(s.binance,"binance",BinanceSource),(s.okx,"okx",OKXSource),
            (s.bitget,"bitget",BitgetSource),(s.gate,"gate",GateSource),(s.bybit,"bybit",BybitSource)]:
            if cfg.enabled:
                src = cls(cfg, HttpClient(timeout=s.http_timeout, proxy=s.proxy, rate_limit_ms=cfg.rate_limit_ms))
                src._cache_ttl = s.market_cache_ttl
                self.sources.append(src); self.source_map[name] = src; self.tracker.register_source(name, src)
        if s.dexscreener_enabled:
            src = DexScreenerSource(s, HttpClient(timeout=s.http_timeout, proxy=s.proxy))
            self.sources.append(src); self.source_map["dexscreener"] = src; self.tracker.register_source("dexscreener", src)

    async def load_caches(self):
        logger.info("Loading market caches...")
        for i, r in enumerate(await asyncio.gather(*(src.load_market_cache() for src in self.sources), return_exceptions=True)):
            src = self.sources[i]
            if isinstance(r, Exception): logger.error(f"❌ {src.name}: {r}")
            elif r == 0 and src.source_type == SignalSourceType.CEX_SPOT: logger.warning(f"⚠️ {src.name}: empty")
            else: logger.info(f"✅ {src.name}: {r}")

    def _resolve(self, md: MarketData) -> BaseDataSource | None:
        sk = md.source.split(":")[0] if ":" in md.source else md.source
        return self.source_map.get("dexscreener" if sk == "dex" else sk)

    async def scan_once(self) -> tuple[list[Signal], list[Signal]]:
        t0 = time.time(); s = self.settings
        logger.info(f"{'='*40}\nScan [{s.scoring_mode}]{' ADA' if self.filter.is_adaptive else ''}")

        # 1. Fetch
        all_md: list[MarketData] = []
        for i, r in enumerate(await asyncio.gather(*(src.fetch_tickers() for src in self.sources), return_exceptions=True)):
            if isinstance(r, list): all_md.extend(r)
            elif isinstance(r, Exception): logger.error(f"{self.sources[i].name}: {r}")
        logger.info(f"Tickers: {len(all_md)}")
        rs = self.filter.new_round(len(all_md))

        # 2. Coarse
        coarse = [md for md in all_md if self.filter.coarse_pre_filter(md)[0]]
        logger.info(f"Coarse: {rs.coarse_passed}")
        if not coarse: self._end(t0,0,0); return [],[]

        # 3. Enrich
        sem = asyncio.Semaphore(5)
        async def _en(md):
            async with sem:
                src = self._resolve(md)
                try: return await src.enrich_multi_period(md) if src else md
                except: return md
        enriched = [r for r in await asyncio.gather(*(_en(md) for md in coarse), return_exceptions=True) if isinstance(r, MarketData)]
        self.filter.set_enriched_count(len(enriched)); logger.info(f"Enriched: {len(enriched)}")

        # ============================================================
        # 4. DUAL FINE FILTER → 两条完全独立的流
        # ============================================================
        main_mds: list[MarketData] = []   # 主推流
        wl_mds: list[MarketData] = []     # 观察池流 (互斥)
        for md in enriched:
            if self.filter.fine_pre_filter(md)[0]:
                main_mds.append(md)
            elif self.filter.fine_pre_filter_watchlist(md)[0]:
                wl_mds.append(md)
        logger.info(f"Fine: main={rs.fine_passed} wl={rs.fine_wl_passed}")

        # ============================================================
        # 5. PRICE VERIFY — 两条流各自独立验证
        # ============================================================
        if s.enable_price_verification:
            vs = asyncio.Semaphore(5)
            async def _pv(md):
                async with vs:
                    if md.validation.signal_source_type == SignalSourceType.CEX_SPOT:
                        src = self._resolve(md)
                        if src: md = await src.validate_price_consistency(md, s.price_deviation_threshold)
                    else: md.validation.price_verified = True
                    return md
            main_mds = [r for r in await asyncio.gather(*(_pv(md) for md in main_mds), return_exceptions=True) if isinstance(r, MarketData)]
            wl_mds = [r for r in await asyncio.gather(*(_pv(md) for md in wl_mds), return_exceptions=True) if isinstance(r, MarketData)]
        else:
            for md in main_mds + wl_mds: md.validation.price_verified = True

        # ============================================================
        # 6. RISK + SCORE — 各自独立
        # ============================================================
        def _rs(pool):
            sigs = []
            for md in pool:
                md = self.risk_mgr.assess_liquidity(md); md = self.risk_mgr.classify_style(md)
                sigs.append(self.scorer.score(md))
            return sigs
        main_sigs = _rs(main_mds)
        wl_sigs = _rs(wl_mds)

        # ============================================================
        # 7. VOLUME ANOMALY — 仅主推流, 降级去观察池流
        # ============================================================
        clean_main: list[Signal] = []
        for sig in main_sigs:
            v = self.filter.check_volume_anomaly(sig)
            if v == "ok": clean_main.append(sig)
            elif v == "demote_watchlist": wl_sigs.append(sig)
            # reject → 丢弃

        # ============================================================
        # 8. VALIDITY — 两条流各自独立过合法性闸门
        # ============================================================
        valid_main: list[Signal] = []
        for sig in clean_main:
            ok, reason = self.filter.execution_validity_gate(sig)
            if ok: valid_main.append(sig); rs.validity_main_passed += 1

        valid_wl: list[Signal] = []
        for sig in wl_sigs:
            ok, reason = self.filter.execution_validity_gate(sig)
            if ok: valid_wl.append(sig); rs.validity_wl_passed += 1
            # 不通过 → 丢弃, 绝不进观察池

        logger.info(f"Validity: main={rs.validity_main_passed} wl={rs.validity_wl_passed} "
                    f"vol:dem={rs.vol_demoted}/rej={rs.vol_rejected}")

        # ============================================================
        # 9. POST-FILTER (主推流)
        # ============================================================
        pushed_cands: list[Signal] = []
        main_overflow: list[Signal] = []  # 主推流中未过post_filter但合法的
        for sig in valid_main:
            ok, _ = self.filter.post_filter(sig)
            if ok: pushed_cands.append(sig)
            else: main_overflow.append(sig)

        # 10. Cross-exchange dedup (V2.9) — 同币多交易所只保留最优
        xd_kept, xd_demoted = self.filter.cross_exchange_dedup(pushed_cands)

        # 主推溢出 + 跨交易所降级 合并到观察池 (已过合法性)
        combined_wl = valid_wl + main_overflow + xd_demoted
        # 去重 + 冷却检查
        seen = set()
        final_wl: list[Signal] = []
        for sig in combined_wl:
            k = f"{sig.symbol}:{sig.source}"
            if k in seen: continue
            seen.add(k)
            if self.filter.is_watchlist_candidate(sig):
                final_wl.append(sig)

        logger.info(f"Post: main={rs.post_filter_passed} wl={len(final_wl)}")

        # 11. Cluster dedup (叙事去重)
        deduped = self.filter.cluster_dedup(xd_kept)
        logger.info(f"Dedup: {rs.cluster_dedup_passed} (xd_demoted={len(xd_demoted)})")

        final_wl.sort(key=lambda sig: sig.score.total_score, reverse=True)
        final_wl = final_wl[:s.max_daily_watchlist]

        # ============================================================
        # 12. PRE-PUSH PRICE RECHECK (V2.9) — 推送前实时价格复核
        # ============================================================
        recheck_passed: list[Signal] = []
        recheck_demoted: list[Signal] = []
        if s.recheck_enabled and deduped:
            rc_sem = asyncio.Semaphore(5)
            async def _recheck(sig: Signal) -> Signal:
                async with rc_sem:
                    src = self._resolve(sig.market_data)
                    if not src:
                        return sig
                    try:
                        klines = await src.fetch_klines(sig.symbol, "1m", 1)
                        if klines:
                            import time as _t
                            rc_price = klines[-1].get("close", 0)
                            if rc_price > 0:
                                dev = abs(rc_price - sig.price) / sig.price * 100
                                sig.market_data.validation.recheck_price = rc_price
                                sig.market_data.validation.recheck_deviation_pct = round(dev, 2)
                                sig.market_data.validation.recheck_time = _t.time()
                    except Exception as e:
                        logger.warning(f"Recheck failed {sig.symbol}: {e}")
                    return sig

            rechecked = await asyncio.gather(*(_recheck(sig) for sig in deduped), return_exceptions=True)
            for r in rechecked:
                if isinstance(r, Exception):
                    continue
                sig = r
                dev = sig.market_data.validation.recheck_deviation_pct
                if dev > s.recheck_max_deviation_pct and sig.market_data.validation.recheck_price > 0:
                    logger.info(f"Recheck DEMOTE {sig.symbol}: dev={dev:.1f}% > {s.recheck_max_deviation_pct}%")
                    recheck_demoted.append(sig)
                else:
                    recheck_passed.append(sig)
        else:
            recheck_passed = deduped

        # 复核降级的进观察池
        for sig in recheck_demoted:
            if self.filter.is_watchlist_candidate(sig):
                final_wl.append(sig)

        logger.info(f"Recheck: passed={len(recheck_passed)} demoted={len(recheck_demoted)}")

        # ============================================================
        # 13. PUSH 主推
        # ============================================================
        n_push = 0
        for sig in recheck_passed:
            self.db.save_signal(sig)
            try:
                await self.notifier.push_signal(sig)
                self.filter.record_push(sig); n_push += 1
                self.tracker.start_tracking(sig)
            except Exception as e: logger.error(f"Push err {sig.symbol}: {e}")

        # ============================================================
        # 14. PUSH 观察池 (正常)
        # ============================================================
        n_wl = 0
        if final_wl:
            for sig in final_wl:
                self.db.save_signal(sig); self.filter.record_watchlist_push(sig); n_wl += 1
            try: await self.notifier.push_watchlist(final_wl)
            except Exception as e: logger.error(f"WL err: {e}")

        # ============================================================
        # 15. FALLBACK — 严格只从 valid_wl 选 (不混入 valid_main)
        # ============================================================
        if n_push == 0 and n_wl == 0 and s.push_watchlist_on_empty_main:
            # 只用观察池流中已过合法性的信号 (valid_wl, 不含 main_overflow)
            fb_pool = [sig for sig in valid_wl
                       if sig.phase != SignalPhase.REJECT and sig.score.total_score > 25]
            # 多因子排序
            fb_ranked = self.filter.rank_for_fallback(fb_pool)
            # 去重
            fb_seen = set()
            fb_final: list[Signal] = []
            for sig in fb_ranked:
                k = f"{sig.symbol}:{sig.source}"
                if k not in fb_seen:
                    fb_seen.add(k); fb_final.append(sig)
                if len(fb_final) >= s.empty_main_watchlist_count: break

            if fb_final:
                logger.info(f"Fallback: {len(fb_final)} from valid_wl only")
                for sig in fb_final:
                    self.db.save_signal(sig); self.filter.record_watchlist_push(sig)
                try: await self.notifier.push_watchlist(fb_final, is_fallback=True)
                except Exception as e: logger.error(f"FB err: {e}")
                n_wl += len(fb_final)

        self.filter.record_round_signal_count(n_push, n_wl)
        self._end(t0, n_push, n_wl)
        return deduped, final_wl

    def _end(self, t0, p, w):
        el = time.time()-t0; rs = self.filter.stats
        logger.info(f"[{el:.1f}s] {rs.total_input}→co{rs.coarse_passed}→en{rs.enriched}→"
            f"fi(m{rs.fine_passed}/w{rs.fine_wl_passed})→val(m{rs.validity_main_passed}/w{rs.validity_wl_passed})→"
            f"post{rs.post_filter_passed}→dup{rs.cluster_dedup_passed}→{p}push+{w}wl")
        logger.info(f"Stats: {rs.summary()}")

    async def check_tracking(self):
        try: await self.tracker.check_pending()
        except Exception as e: logger.error(f"Track: {e}")

    async def run_loop(self):
        s = self.settings
        logger.info(f"🚀 V2.9 | {s.scoring_mode}")
        await self.load_caches()
        await self.notifier.push_text(
            f"🚀 <b>Crypto Radar V2.9</b>\n"
            f"模式:{s.scoring_mode} 间隔:{s.scan_interval_seconds}s\n"
            f"源:{','.join(src.name for src in self.sources)}\n"
            f"价格校验:{'开' if s.enable_price_verification else '关'} "
            f"观察池:{'开' if s.watchlist_enabled else '关'} "
            f"自适应:{'开' if s.adaptive_enabled else '关'}")
        cycle = 0
        while True:
            cycle += 1
            try:
                await self.scan_once()
                if cycle%3==0: await self.check_tracking()
                if cycle%100==0:
                    st = self.tracker.get_stats()
                    if st.get("total_completed",0)>0: await self.notifier.push_stats(st)
            except KeyboardInterrupt: break
            except Exception as e: logger.error(f"Loop: {e}", exc_info=True)
            await asyncio.sleep(s.scan_interval_seconds)

    async def validate_symbol(self, ex: str, sym: str):
        src = self.source_map.get(ex)
        if not src: print(f"❌ {ex} 不可用"); return
        print(f"\n{'='*40}\n{ex}:{sym}\n{'='*40}")
        n = await src.load_market_cache()
        print(f"Cache:{n} ok={src.cache_available}")
        a = src.is_symbol_active(sym); info = src.get_symbol_info(sym)
        print(f"Active:{'✅' if a else '❌'}")
        if info: print(f"  raw={info.raw_symbol} status={info.status}")
        try:
            kl = await src.fetch_klines(sym,"1m",2)
            print(f"1m close:{kl[-1]['close']:.6g}" if kl else "❌ no kline")
        except Exception as e: print(f"❌ {e}")
        print(f"Verdict: {'✅' if a and src.cache_available else '❌'}\n")

    async def export_csv(self): self.db.export_signals_csv(); self.db.export_tracking_csv()
    async def show_stats(self):
        st = self.tracker.get_stats()
        print(f"\n📊 完成{st.get('total_completed',0)} 胜率{st.get('win_rate',0):.1f}%")
        for g in "ABC":
            d=st.get(f"grade_{g}",{}); t=d.get("total",0)
            if t: print(f"  {g}:{t}次 胜率{d['win_rate']:.0f}% 均盈{d['avg_24h_pct']:+.1f}%")
    async def cleanup(self):
        for src in self.sources:
            try: await src.close()
            except: pass
        self.db.close()

async def main():
    pa = argparse.ArgumentParser()
    pa.add_argument("--once", action="store_true")
    pa.add_argument("--export", action="store_true")
    pa.add_argument("--stats", action="store_true")
    pa.add_argument("--validate-symbol", nargs=2, metavar=("EX","SYM"))
    a = pa.parse_args()
    settings = load_settings(); setup_logging(settings)
    radar = CryptoRadar(settings)
    try:
        if a.validate_symbol: await radar.validate_symbol(a.validate_symbol[0].lower(), a.validate_symbol[1].upper())
        elif a.export: await radar.export_csv()
        elif a.stats: await radar.show_stats()
        elif a.once: await radar.load_caches(); p,w = await radar.scan_once(); print(f"Push {len(p)}, WL {len(w)}")
        else: await radar.run_loop()
    finally: await radar.cleanup()

if __name__ == "__main__": asyncio.run(main())
