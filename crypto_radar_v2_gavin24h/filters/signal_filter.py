"""
Crypto Radar V2 - 信号过滤器 (V2.8)
cooldown key=symbol:source, 多因子fallback排序, 多周期量比联合判断
"""
from __future__ import annotations
import time
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from models import MarketData, Signal, SignalPhase, MarketStatus, SignalSourceType
from config.settings import Settings

logger = logging.getLogger("radar.filter")
CEX_SOURCE_NAMES = {"binance", "okx", "bitget", "gate", "bybit"}

COARSE_PARAMS = {
    "strict":      {"min_24h_turnover": 500_000, "min_24h_change_abs": 1.0, "max_24h_change": 150.0},
    "balanced":    {"min_24h_turnover": 200_000, "min_24h_change_abs": 0.5, "max_24h_change": 200.0},
    "calibration": {"min_24h_turnover": 80_000,  "min_24h_change_abs": 0.0, "max_24h_change": 500.0},
}
SOURCE_TURNOVER_MULT = {"binance":1.0,"okx":1.0,"bitget":0.6,"gate":0.5,"bybit":0.7,"dex":0.3,"dexscreener":0.3}
FINE_PARAMS = {
    "strict":      {"min_change_5m": 1.5, "min_change_15m": 3.0, "max_change_1h": 50.0},
    "balanced":    {"min_change_5m": 0.8, "min_change_15m": 2.0, "max_change_1h": 60.0},
    "calibration": {"min_change_5m": 0.4, "min_change_15m": 1.0, "max_change_1h": 80.0},
}
SOURCE_CHANGE_MULT = {"binance":1.0,"okx":1.0,"bitget":0.9,"gate":0.85,"bybit":0.9,"dex":0.8,"dexscreener":0.8}

def _src_key(source: str) -> str:
    return "dex" if source.startswith("dex:") else source.split(":")[0].lower()

def _cd_key(symbol: str, source: str) -> str:
    """冷却主键 = symbol:exchange, 允许跨交易所共振"""
    return f"{symbol}:{_src_key(source)}"


def _base_symbol(symbol: str) -> str:
    return symbol.split("/")[0].upper() if "/" in symbol else symbol.upper()

def _is_leverage_symbol(symbol: str) -> bool:
    base = _base_symbol(symbol)
    return any(base.endswith(suf) for suf in ("3L", "3S", "5L", "5S"))


@dataclass
class CooldownEntry:
    last_main_time: float = 0
    last_wl_time: float = 0
    last_phase: str = ""
    last_price: float = 0
    last_score: float = 0


@dataclass
class RejectionStats:
    total_input: int = 0
    coarse_passed: int = 0
    enriched: int = 0
    fine_passed: int = 0
    fine_wl_passed: int = 0
    validity_main_passed: int = 0
    validity_wl_passed: int = 0
    post_filter_passed: int = 0
    cluster_dedup_passed: int = 0
    vol_demoted: int = 0
    vol_rejected: int = 0
    adaptive_active: bool = False

    coarse_rej: dict[str,int] = field(default_factory=lambda: defaultdict(int))
    fine_rej: dict[str,int] = field(default_factory=lambda: defaultdict(int))
    validity_rej: dict[str,int] = field(default_factory=lambda: defaultdict(int))
    post_rej: dict[str,int] = field(default_factory=lambda: defaultdict(int))

    invalid_symbol_count: int = 0
    inactive_market_count: int = 0
    unverified_price_count: int = 0
    source_type_conflict_count: int = 0
    dex_unknown_market_count: int = 0

    def summary(self) -> str:
        p = [f"{self.total_input}→co{self.coarse_passed}→en{self.enriched}→"
             f"fi(m{self.fine_passed}/w{self.fine_wl_passed})→"
             f"val(m{self.validity_main_passed}/w{self.validity_wl_passed})→"
             f"post{self.post_filter_passed}→dup{self.cluster_dedup_passed}"]
        if self.vol_demoted or self.vol_rejected:
            p.append(f"vol:dem{self.vol_demoted}/rej{self.vol_rejected}")
        if self.adaptive_active: p.append("ADA")
        for label, d in [("co",self.coarse_rej),("fi",self.fine_rej),("val",self.validity_rej),("po",self.post_rej)]:
            if d:
                top = sorted(d.items(), key=lambda x:-x[1])[:3]
                p.append(f"{label}:" + ",".join(f"{k}({v})" for k,v in top))
        return " | ".join(p)


class SignalFilter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mode = getattr(settings, "scoring_mode", "balanced")
        if self.mode not in COARSE_PARAMS: self.mode = "balanced"
        self.is_calibration = self.mode == "calibration"
        self.coarse_p = COARSE_PARAMS[self.mode]
        self.fine_p = FINE_PARAMS[self.mode]

        self._cd: dict[str, CooldownEntry] = {}  # key = symbol:exchange
        self._daily_push: int = 0
        self._daily_wl: int = 0
        self._daily_date: str = ""
        self._low_q_streak: int = 0
        self._stats: RejectionStats = RejectionStats()
        self._thr_logged: bool = False
        self._recent_counts: deque[int] = deque(maxlen=settings.adaptive_lookback_cycles)
        self._adaptive: bool = False

    def new_round(self, total: int) -> RejectionStats:
        self._stats = RejectionStats(total_input=total)
        self._thr_logged = False
        self._check_adaptive()
        self._stats.adaptive_active = self._adaptive
        return self._stats

    def record_round_signal_count(self, main: int, wl: int):
        self._recent_counts.append(main + wl)

    @property
    def stats(self) -> RejectionStats: return self._stats
    @property
    def is_adaptive(self) -> bool: return self._adaptive

    def _check_adaptive(self):
        s = self.settings
        if not s.adaptive_enabled or len(self._recent_counts) < 5:
            self._adaptive = False; return
        was = self._adaptive
        self._adaptive = sum(self._recent_counts) < s.adaptive_min_signals_threshold
        if self._adaptive and not was: logger.info(f"Adaptive ON")
        elif not self._adaptive and was: logger.info("Adaptive OFF")

    def _log_thresholds(self):
        if self._thr_logged: return
        self._thr_logged = True
        a = " [ADA]" if self._adaptive else ""
        logger.info(f"[{self.mode}]{a} CO:${self.coarse_p['min_24h_turnover']:.0f} FI:5m>{self.fine_p['min_change_5m']}%")

    # =================== 粗预过滤 ===================
    def coarse_pre_filter(self, md: MarketData) -> tuple[bool, str]:
        self._log_thresholds()
        s, p, cp = self.settings, md.periods, self.coarse_p
        base = md.symbol.split("/")[0] if "/" in md.symbol else md.symbol
        base_up = _base_symbol(md.symbol)
        is_leverage = _is_leverage_symbol(md.symbol) and base_up not in s.leverage_allowlist
        st = self._stats

        if base in s.blacklist: st.coarse_rej["黑名单"]+=1; return False,"黑名单"
        if md.price <= 0: st.coarse_rej["价格"]+=1; return False,"价格<=0"

        sk = _src_key(md.source)
        is_dex = sk in ("dex","dexscreener")
        if is_dex and md.chain_data:
            cd = md.chain_data
            if cd.liquidity_usd < s.dex_min_liquidity: st.coarse_rej["dex_liq"]+=1; return False,"dex_liq"
            if cd.txns_1h < s.dex_min_txns_1h: st.coarse_rej["dex_txn"]+=1; return False,"dex_txn"
            if cd.pool_age_hours < s.dex_min_pool_age_hours: st.coarse_rej["dex_age"]+=1; return False,"dex_age"
        else:
            tm = SOURCE_TURNOVER_MULT.get(sk,1.0)
            eff = cp["min_24h_turnover"] * tm * (0.8 if self._adaptive else 1.0)
            if p.turnover_24h > 0 and p.turnover_24h < eff:
                st.coarse_rej["24h额"]+=1; return False,f"${p.turnover_24h:.0f}<${eff:.0f}"

        thr = cp["min_24h_change_abs"] * (0.5 if self._adaptive else 1.0)
        if thr > 0 and abs(p.change_24h) < thr and base not in s.whitelist:
            st.coarse_rej["24h小"]+=1; return False,f"|24h|<{thr}%"
        if p.change_24h > cp["max_24h_change"]:
            st.coarse_rej["24h热"]+=1; return False,"24h极热"

        st.coarse_passed += 1; return True,"ok"

    # =================== 细预过滤 ===================
    def fine_pre_filter(self, md: MarketData) -> tuple[bool, str]:
        return self._fine(md, False)
    def fine_pre_filter_watchlist(self, md: MarketData) -> tuple[bool, str]:
        return self._fine(md, True)

    def _fine(self, md: MarketData, wl: bool) -> tuple[bool, str]:
        s, p, fp = self.settings, md.periods, self.fine_p
        base = md.symbol.split("/")[0] if "/" in md.symbol else md.symbol
        base_up = _base_symbol(md.symbol)
        is_leverage = _is_leverage_symbol(md.symbol) and base_up not in s.leverage_allowlist
        cm = SOURCE_CHANGE_MULT.get(_src_key(md.source), 1.0)
        e5 = fp["min_change_5m"] * cm
        e15 = fp["min_change_15m"] * cm
        emax = fp["max_change_1h"]
        if wl:
            e5 *= s.watchlist_fine_change_mult; e15 *= s.watchlist_fine_change_mult; emax *= 1.3
        if self._adaptive:
            e5 *= s.adaptive_fine_change_relax; e15 *= s.adaptive_fine_change_relax

        hit = p.change_5m >= e5 or p.change_15m >= e15
        if base in s.whitelist:
            hit = hit or p.change_5m >= e5*0.5 or p.change_15m >= e15*0.5
        if not hit and p.change_24h > 5 and p.change_5m > e5*0.5: hit = True
        if not hit and p.volume_ratio_5m > 3 and p.change_5m > e5*0.6: hit = True
        if not hit and wl and p.change_1h > e15: hit = True

        if is_leverage and not wl and s.leverage_watchlist_only:
            self._stats.fine_rej["杠杆主推禁用"] += 1
            return False, "杠杆代币仅观察池"

        if is_leverage and wl:
            # 杠杆代币进入观察池也更严格
            if p.change_1h <= 0 or p.change_4h <= 0 or p.change_24h > s.watchlist_reject_if_24h_overheat:
                self._stats.fine_rej["杠杆观察过热"] += 1
                return False, "杠杆结构不足"

        if not hit:
            tag = "涨幅" if not wl else "WL涨幅"
            self._stats.fine_rej[tag]+=1; return False,f"5m={p.change_5m:.2f}%"
        if p.change_1h > emax:
            self._stats.fine_rej["1h热"]+=1; return False,f"1h>{emax:.0f}%"

        if wl: self._stats.fine_wl_passed += 1
        else: self._stats.fine_passed += 1
        return True,"ok"

    # =================== 量比降级 (多周期联合) ===================
    def check_volume_anomaly(self, signal: Signal) -> str:
        """'ok' / 'demote_watchlist' / 'reject'"""
        s = self.settings
        p = signal.market_data.periods
        vr5 = p.volume_ratio_5m
        vr15 = p.volume_ratio_15m
        st = self._stats

        if vr5 < s.volume_ratio_warning_threshold:
            return "ok"

        # 多周期结构评估: 越健康越给缓冲
        structure_score = 0
        # 1h/4h协调且非纯脉冲
        if 2.0 < p.change_1h < 20.0 and p.change_4h > 1.0: structure_score += 2
        # 15m量比也配合(不是只有5m异常)
        if vr15 > 2.0 and vr15 < s.volume_ratio_warning_threshold: structure_score += 1
        # 流动性充足
        if p.turnover_24h > 2_000_000: structure_score += 1
        # 不是纯5m脉冲
        if p.change_5m < 8 and p.change_15m > 1.5: structure_score += 1
        # 24h不过热
        if p.change_24h < 25: structure_score += 1

        # structure_score: 0-6, >=4算有良好结构
        has_good_structure = structure_score >= 4

        if vr5 >= s.volume_ratio_extreme_threshold:
            # >100x: 有结构最多降级, 无结构拒绝
            if has_good_structure:
                st.vol_demoted += 1; return "demote_watchlist"
            st.vol_rejected += 1; return "reject"

        if vr5 >= s.volume_ratio_reject_threshold:
            # >60x: 有结构降级, 无结构拒绝
            if has_good_structure:
                st.vol_demoted += 1; return "demote_watchlist"
            st.vol_rejected += 1; return "reject"

        if vr5 >= s.volume_ratio_heavy_threshold:
            # >30x: 降级到观察池
            st.vol_demoted += 1; return "demote_watchlist"

        # 15-30x: 扣分在scorer处理, 允许通过
        return "ok"

    # =================== 合法性闸门 ===================
    def execution_validity_gate(self, signal: Signal) -> tuple[bool, str]:
        v = signal.market_data.validation
        st = self._stats
        sk = _src_key(signal.source)
        is_dex = sk in ("dex","dexscreener")
        is_cex = sk in CEX_SOURCE_NAMES

        if v.signal_source_type == SignalSourceType.UNKNOWN:
            st.validity_rej["type_unk"]+=1; return False,"type_unknown"
        if is_dex and v.signal_source_type == SignalSourceType.CEX_SPOT:
            st.validity_rej["type_dex"]+=1; st.source_type_conflict_count+=1; return False,"type_conflict_dex"
        if is_cex and v.signal_source_type != SignalSourceType.CEX_SPOT:
            st.validity_rej["type_cex"]+=1; st.source_type_conflict_count+=1; return False,"type_conflict_cex"

        if v.signal_source_type == SignalSourceType.CEX_SPOT:
            if not v.symbol_validated:
                st.validity_rej["inv_sym"]+=1; st.invalid_symbol_count+=1; return False,"invalid_symbol"
            if v.market_status != MarketStatus.ACTIVE:
                st.validity_rej["inactive"]+=1; st.inactive_market_count+=1; return False,"inactive"
            if not v.price_verified:
                st.validity_rej["px_unv"]+=1; st.unverified_price_count+=1; return False,"unverified_price"
            if not signal.market_data.trade_url:
                st.validity_rej["no_url"]+=1; return False,"no_url"

        if v.signal_source_type == SignalSourceType.DEX:
            if v.market_status == MarketStatus.UNKNOWN:
                st.validity_rej["dex_unk"]+=1; st.dex_unknown_market_count+=1; return False,"dex_unknown"
            if v.market_status in (MarketStatus.INACTIVE, MarketStatus.DELISTED):
                st.validity_rej["dex_off"]+=1; return False,"dex_inactive"

        return True,"ok"

    # =================== 冷却 (key=symbol:source) ===================
    def check_main_cooldown(self, signal: Signal) -> tuple[bool, str]:
        return self._check_cd(signal, True)
    def check_watchlist_cooldown(self, signal: Signal) -> tuple[bool, str]:
        return self._check_cd(signal, False)

    def _check_cd(self, sig: Signal, is_main: bool) -> tuple[bool, str]:
        s = self.settings
        now = time.time()
        key = _cd_key(sig.symbol, sig.source)
        ent = self._cd.get(key)
        if not ent: return True,""

        cd_s = s.main_push_cooldown if is_main else s.watchlist_cooldown
        ref = ent.last_main_time if is_main else max(ent.last_main_time, ent.last_wl_time)
        if now - ref >= cd_s: return True,""

        # 重置条件
        if s.cooldown_phase_change_reset and ent.last_phase and sig.phase.value != ent.last_phase:
            return True,"phase"
        if ent.last_price > 0 and s.cooldown_price_change_reset_pct > 0:
            if abs(sig.price - ent.last_price) / ent.last_price * 100 >= s.cooldown_price_change_reset_pct:
                return True,"price"
        if ent.last_score > 0 and s.cooldown_score_jump_reset > 0:
            if sig.score.total_score - ent.last_score >= s.cooldown_score_jump_reset:
                return True,"score"

        return False, f"cd{int(cd_s-(now-ref))}s"

    # =================== 后过滤 ===================
    def post_filter(self, signal: Signal) -> tuple[bool, str]:
        s = self.settings
        if self.is_calibration:
            if signal.phase == SignalPhase.REJECT:
                self._stats.post_rej["放弃"]+=1; return False,"放弃"
            self._stats.post_filter_passed += 1; return True,"cal"

        if signal.score.total_score < s.push_min_score:
            self._stats.post_rej["分数"]+=1; return False,f"{signal.score.total_score:.0f}<{s.push_min_score}"
        if signal.phase == SignalPhase.REJECT:
            self._stats.post_rej["放弃"]+=1; return False,"放弃"

        p = signal.market_data.periods
        if _is_leverage_symbol(signal.symbol) and _base_symbol(signal.symbol) not in s.leverage_allowlist and s.leverage_watchlist_only:
            self._stats.post_rej["杠杆"] += 1; return False, "杠杆代币主推禁用"
        if s.main_require_positive_1h and p.change_1h <= 0:
            self._stats.post_rej["1h弱"] += 1; return False, "1h不为正"
        if s.main_require_positive_4h and p.change_4h <= 0:
            self._stats.post_rej["4h弱"] += 1; return False, "4h不为正"
        if p.change_24h > s.main_max_24h_change:
            self._stats.post_rej["24h热"] += 1; return False, f"24h>{s.main_max_24h_change:.0f}%"
        if p.change_1h > s.main_max_1h_change:
            self._stats.post_rej["1h热"] += 1; return False, f"1h>{s.main_max_1h_change:.1f}%"
        if p.turnover_24h > 0 and p.turnover_24h < s.main_min_turnover_24h:
            self._stats.post_rej["24h额不足"] += 1; return False, f"24h额<${s.main_min_turnover_24h:.0f}"
        if p.turnover_1h > 0 and p.turnover_1h < s.main_min_turnover_1h:
            self._stats.post_rej["1h额不足"] += 1; return False, f"1h额<${s.main_min_turnover_1h:.0f}"
        if p.volume_ratio_5m >= s.volume_ratio_heavy_threshold and p.change_15m < 1.5:
            self._stats.post_rej["量比异常"] += 1; return False, "疑似纯脉冲/异常量比"

        ok, r = self.check_main_cooldown(signal)
        if not ok: self._stats.post_rej["cd"]+=1; return False,r
        self._ensure_daily()
        if self._daily_push >= s.max_daily_pushes:
            self._stats.post_rej["上限"]+=1; return False,"上限"
        if self._low_q_streak >= s.low_quality_fuse_threshold:
            self._stats.post_rej["熔断"]+=1; return False,"熔断"
        self._stats.post_filter_passed += 1; return True,"ok"

    # =================== Watchlist候选 ===================
    def is_watchlist_candidate(self, signal: Signal) -> bool:
        """调用方必须已确保 signal 通过合法性校验"""
        s = self.settings
        wl_min = s.watchlist_min_score
        if self._adaptive: wl_min = max(wl_min - s.adaptive_watchlist_score_relax, 15)
        if signal.phase == SignalPhase.REJECT: return False
        if signal.score.total_score < wl_min: return False
        p = signal.market_data.periods
        if _is_leverage_symbol(signal.symbol) and _base_symbol(signal.symbol) not in s.leverage_allowlist:
            if p.change_1h <= 0 or p.change_4h <= 0:
                return False
        if p.change_24h > s.watchlist_reject_if_24h_overheat:
            return False
        ok, _ = self.check_watchlist_cooldown(signal)
        if not ok: return False
        self._ensure_daily()
        return self._daily_wl < s.max_daily_watchlist

    # =================== Fallback 排序 ===================
    @staticmethod
    def rank_for_fallback(signals: list[Signal]) -> list[Signal]:
        """多因子排序: 适合24h > 结构协调 > 非脉冲 > 分数"""
        def _key(sig: Signal) -> tuple:
            p = sig.market_data.periods
            adv = sig.advice
            # 因子1: 适合24h短线 (bool → int)
            f1 = 1 if adv.is_suitable_for_24h_trade else 0
            # 因子2: 1h/4h协调 (bool)
            f2 = 1 if (2 < p.change_1h < 15 and 1 < p.change_4h < 20 and p.change_24h < 30) else 0
            # 因子3: 非纯脉冲 (bool)
            f3 = 1 if not (p.change_5m > 5 and p.change_15m < 1.5) else 0
            # 因子4: 24h不过热 (bool)
            f4 = 1 if p.change_24h < 25 else 0
            # 因子5: 流动性足够 (bool)
            f5 = 1 if p.turnover_24h > 500_000 else 0
            # 因子6: 总分
            f6 = sig.score.total_score
            return (f1, f2, f3, f4, f5, f6)
        return sorted(signals, key=_key, reverse=True)

    # =================== 去重 ===================
    def cluster_dedup(self, signals: list[Signal]) -> list[Signal]:
        mpg = self.settings.cluster_max_per_group
        if self.is_calibration: mpg = max(mpg, 5)
        groups: dict[str, list[Signal]] = defaultdict(list)
        for sig in signals:
            tags = []
            if sig.narrative_tag: tags.append(f"n:{sig.narrative_tag}")
            if sig.chain_tag: tags.append(f"c:{sig.chain_tag}")
            if sig.market_data.style_tag: tags.append(f"s:{sig.market_data.style_tag}")
            groups["|".join(sorted(tags)) if tags else f"o:{sig.symbol}"].append(sig)
        result = []
        for g in groups.values():
            g.sort(key=lambda s: s.advice.execution_priority_score, reverse=True)
            result.extend(g[:mpg])
        result.sort(key=lambda s: s.advice.execution_priority_score, reverse=True)
        self._stats.cluster_dedup_passed = len(result)
        return result

    # =================== 记录 ===================
    def record_push(self, sig: Signal):
        key = _cd_key(sig.symbol, sig.source)
        e = self._cd.setdefault(key, CooldownEntry())
        e.last_main_time = time.time(); e.last_phase = sig.phase.value
        e.last_price = sig.price; e.last_score = sig.score.total_score
        self._daily_push += 1; sig.pushed = True
        self._low_q_streak = 0 if sig.score.total_score >= 50 else self._low_q_streak + 1

    def record_watchlist_push(self, sig: Signal):
        key = _cd_key(sig.symbol, sig.source)
        e = self._cd.setdefault(key, CooldownEntry())
        e.last_wl_time = time.time(); e.last_phase = sig.phase.value
        e.last_price = sig.price; e.last_score = sig.score.total_score
        self._daily_wl += 1

    def set_enriched_count(self, n: int): self._stats.enriched = n

    def _ensure_daily(self):
        d = time.strftime("%Y-%m-%d")
        if d != self._daily_date:
            self._daily_push = 0; self._daily_wl = 0; self._daily_date = d
