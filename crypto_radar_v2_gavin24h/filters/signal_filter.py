"""
Crypto Radar V2 - 信号过滤器 (V2.9.1)
cooldown key=symbol:source, 多因子fallback排序, 多周期量比联合判断
V2.9.1: 弱修复过滤, 24h高位过滤, 收紧观察池/fallback, market regime, single-best
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
        # V2.9.1: 大盘环境
        self._market_regime: str = "neutral"  # "bullish" / "neutral" / "bearish"

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
    @property
    def market_regime(self) -> str: return self._market_regime

    def set_market_regime(self, regime: str):
        """由 main.py 每轮扫描开始时根据 BTC/ETH 状态设置"""
        old = self._market_regime
        self._market_regime = regime
        if regime != old:
            logger.info(f"MarketRegime: {old} → {regime}")

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

        # V3.1.1: 杠杆代币全局禁推 — 主推和观察池都直接拒绝
        if is_leverage and s.leverage_token_full_block:
            self._stats.fine_rej["杠杆禁推"] += 1
            return False, "杠杆代币全局禁推"

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
        sk = _src_key(signal.source)
        is_dex = sk in ("dex", "dexscreener")

        # V3.1.1: 杠杆代币全局禁推
        if _is_leverage_symbol(signal.symbol) and _base_symbol(signal.symbol) not in s.leverage_allowlist:
            if s.leverage_token_full_block:
                self._stats.post_rej["杠杆禁推"] += 1; return False, "杠杆代币全局禁推"

        # V3.1: DEX禁推
        if is_dex and s.dex_push_disabled:
            self._stats.post_rej["DEX禁推"] += 1; return False, "DEX代币不进主推"

        # V3.1: 主推仅CEX现货
        if getattr(s, 'require_cex_only_for_main', True) and not is_dex and sk not in CEX_SOURCE_NAMES:
            self._stats.post_rej["非CEX"] += 1; return False, f"非CEX源({sk})不进主推"
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

        # V2.9: 纯5m脉冲加强 — 5m暴涨但1h几乎不动
        if p.change_5m > s.pulse_5m_change_threshold and p.change_1h < s.pulse_5m_1h_max:
            self._stats.post_rej["纯脉冲"] += 1; return False, f"纯5m脉冲(5m={p.change_5m:.1f}% 1h={p.change_1h:.1f}%)"

        # V2.9: 假突破检测 — 5m在涨但4h方向为负(逆大周期)
        if p.change_5m > s.fake_breakout_5m_min and p.change_4h < 0:
            self._stats.post_rej["假突破"] += 1; return False, f"疑似假突破(5m={p.change_5m:.1f}% 4h={p.change_4h:.1f}%)"

        # V2.9: 24h过热尾段 — 24h涨幅高但近4h无延续(涨幅集中在早期)
        if p.change_24h > s.tail_surge_24h_min and p.change_4h < 2.0:
            self._stats.post_rej["尾段过热"] += 1; return False, f"24h尾段无延续(24h={p.change_24h:.1f}% 4h={p.change_4h:.1f}%)"

        # V2.9.1: 弱修复/死猫跳 — 24h跌+1h微涨+量比不足=无力反弹
        if (0 < p.change_1h <= s.weak_repair_max_1h
            and p.change_4h < s.weak_repair_max_4h
            and p.volume_ratio_5m < s.weak_repair_min_vr5m
            and p.change_24h < 0):
            self._stats.post_rej["弱修复"] += 1
            return False, f"弱修复(1h={p.change_1h:.1f}% 4h={p.change_4h:.1f}% vr={p.volume_ratio_5m:.1f}x 24h={p.change_24h:.1f}%)"

        # V3.0: 24h区间位置硬门槛 — 位置太高直接拒绝
        pos = getattr(p, 'position_in_24h_range', 0.5)
        if pos >= s.main_max_position:
            self._stats.post_rej["高位拒绝"] += 1
            return False, f"24h区间位置{pos:.0%}>{s.main_max_position:.0%}"

        # V3.0: 慢修复 — 24h跌+1h<3%+4h<4%→结构太弱不可主推
        if p.change_24h < 0 and p.change_1h < s.slow_repair_min_1h and p.change_4h < s.slow_repair_min_4h:
            self._stats.post_rej["慢修复"] += 1
            return False, f"慢修复(1h={p.change_1h:.1f}% 4h={p.change_4h:.1f}% 24h={p.change_24h:.1f}%)"

        # V3.0: 纯15m脉冲 — 15m暴涨但1h/4h不跟
        if s.pure_15m_pulse_reject and p.change_15m > 6 and p.change_1h < 3 and p.change_4h < 2:
            self._stats.post_rej["15m脉冲"] += 1
            return False, f"纯15m脉冲(15m={p.change_15m:.1f}% 1h={p.change_1h:.1f}%)"

        # V3.0: 高位反抽强惩罚 — 24h已热+位置高+4h弱
        if s.high_position_bounce_penalty and pos >= 0.7 and p.change_24h > 8 and p.change_4h < 3:
            self._stats.post_rej["高位反抽"] += 1
            return False, f"高位反抽(pos={pos:.0%} 24h={p.change_24h:.1f}% 4h={p.change_4h:.1f}%)"

        # V3.0: 大盘环境 — 4级门控
        regime = self._market_regime
        if s.market_regime_enabled:
            if regime == "bearish":
                if signal.score.total_score < s.market_regime_bearish_push_min_score:
                    self._stats.post_rej["大盘弱"] += 1
                    return False, f"bearish需{s.market_regime_bearish_push_min_score:.0f}分"
                if s.bearish_disable_small_cap_main and sk in ("gate", "bybit", "dex", "dexscreener"):
                    self._stats.post_rej["bearish小所"] += 1
                    return False, f"bearish关闭小所({sk})主推"
            elif regime == "weak_neutral":
                if s.weak_neutral_strict_main:
                    if p.turnover_24h > 0 and p.turnover_24h < s.weak_neutral_min_turnover_24h:
                        self._stats.post_rej["weak_neutral低额"] += 1
                        return False, f"weak_neutral需24h额>{s.weak_neutral_min_turnover_24h/1e6:.0f}M"
                    if sk in ("gate", "bybit", "dex", "dexscreener") and p.turnover_24h < 3_000_000:
                        self._stats.post_rej["weak_neutral小所"] += 1
                        return False, f"weak_neutral小所({sk})流动性不足"

        ok, r = self.check_main_cooldown(signal)
        if not ok: self._stats.post_rej["cd"]+=1; return False,r
        self._ensure_daily()
        if self._daily_push >= s.max_daily_pushes:
            self._stats.post_rej["上限"]+=1; return False,"上限"
        if self._low_q_streak >= s.low_quality_fuse_threshold:
            self._stats.post_rej["熔断"]+=1; return False,"熔断"
        self._stats.post_filter_passed += 1; return True,"ok"

    # =================== Watchlist = 监控池 (V3.0) ===================
    def is_watchlist_candidate(self, signal: Signal) -> bool:
        """V3.0: 观察池=监控池, 只收真正值得继续盯的, 不是实盘备选"""
        s = self.settings
        wl_min = max(s.watchlist_min_score, s.watchlist_min_score_strict)
        if self._adaptive: wl_min = max(wl_min - s.adaptive_watchlist_score_relax, 25)
        if signal.phase == SignalPhase.REJECT: return False
        if signal.score.total_score < wl_min: return False
        p = signal.market_data.periods
        sk = _src_key(signal.source)
        is_dex = sk in ("dex", "dexscreener")

        # V3.1.1: 杠杆代币全局禁止进观察池
        if _is_leverage_symbol(signal.symbol) and _base_symbol(signal.symbol) not in s.leverage_allowlist:
            if s.leverage_token_full_block:
                return False
        # V3.1: DEX禁止进观察池
        if is_dex and s.dex_watchlist_disabled: return False
        # V3.1: 观察池仅CEX
        if getattr(s, 'watchlist_require_cex_only', True) and is_dex: return False

        # 1h必须为正
        if s.watchlist_require_positive_1h and p.change_1h <= 0: return False
        # 24h过热
        if p.change_24h > s.watchlist_reject_if_24h_overheat: return False

        # 弱修复不收
        if (0 < p.change_1h <= s.weak_repair_max_1h and p.change_4h < s.weak_repair_max_4h
            and p.volume_ratio_5m < s.weak_repair_min_vr5m and p.change_24h < 0):
            return False
        # 慢修复不收
        if s.watchlist_reject_slow_repair and p.change_24h < 0:
            if p.change_1h < s.slow_repair_min_1h and p.change_4h < s.slow_repair_min_4h:
                return False
        # 无量修复不收
        if s.watchlist_reject_no_volume_repair and p.change_24h < 0:
            if p.volume_ratio_5m < 1.3 and p.volume_ratio_15m < 1.2: return False

        # 24h高位不收
        pos = getattr(p, 'position_in_24h_range', 0.5)
        if pos >= s.watchlist_max_position: return False
        # 高位反抽不收
        if s.watchlist_reject_high_position_bounce and pos >= 0.65 and p.change_24h > 5 and p.change_4h < 2:
            return False

        # 流动性门槛
        if p.turnover_24h > 0 and p.turnover_24h < s.watchlist_min_turnover_24h: return False
        # 24h转正但1h/4h弱 = 假止跌, 不收
        if s.watchlist_require_real_continuation and p.change_24h > 2 and p.change_1h < 2 and p.change_4h < 2:
            return False
        # 纯脉冲不收
        if p.change_5m > s.pulse_5m_change_threshold and p.change_1h < s.pulse_5m_1h_max: return False
        # 量比不一致(5m高15m不跟)不收
        if p.volume_ratio_5m > 5 and p.volume_ratio_15m < 1.3: return False

        # V3.1: 弱市小所不进观察池
        if getattr(s, 'watchlist_reject_small_exchange', True):
            regime = self._market_regime
            if regime in ("bearish", "weak_neutral") and sk in ("gate", "bybit"):
                return False

        # 大盘环境
        regime = self._market_regime
        if s.market_regime_enabled:
            if regime == "bearish":
                if signal.score.total_score < s.market_regime_bearish_wl_min_score: return False
                if s.bearish_strict_watchlist:
                    if sk in ("gate", "bybit", "dex", "dexscreener"): return False
            elif regime == "weak_neutral" and s.weak_neutral_strict_watchlist:
                if signal.score.total_score < 45: return False
                if sk in ("gate", "bybit") and p.turnover_24h < 3_000_000: return False

        ok, _ = self.check_watchlist_cooldown(signal)
        if not ok: return False
        self._ensure_daily()
        max_wl = min(s.max_daily_watchlist, s.max_daily_watchlist_strict)
        return self._daily_wl < max_wl

    # =================== Fallback 排序 ===================
    @staticmethod
    def rank_for_fallback(signals: list[Signal]) -> list[Signal]:
        """V2.9.1: 多因子排序+更严入池条件: 适合24h > 结构协调 > 非脉冲 > 低位 > 分数"""
        def _key(sig: Signal) -> tuple:
            p = sig.market_data.periods
            adv = sig.advice
            f1 = 1 if adv.is_suitable_for_24h_trade else 0
            f2 = 1 if (2 < p.change_1h < 15 and 1 < p.change_4h < 20 and p.change_24h < 30) else 0
            f3 = 1 if not (p.change_5m > 5 and p.change_15m < 1.5) else 0
            f4 = 1 if p.change_24h < 25 else 0
            f5 = 1 if p.turnover_24h > 500_000 else 0
            # V2.9.1: 24h区间位置 — 低位优先
            pos = getattr(p, 'position_in_24h_range', 0.5)
            f6 = 1 if pos < 0.7 else 0
            f7 = sig.score.total_score
            return (f1, f2, f3, f4, f5, f6, f7)
        return sorted(signals, key=_key, reverse=True)

    def filter_fallback_pool(self, signals: list[Signal]) -> list[Signal]:
        """V3.0: fallback几乎不用 — 只有极高质量才进, 否则宁可空轮"""
        s = self.settings
        # bearish时直接关闭fallback
        if s.market_regime_enabled and self._market_regime == "bearish" and s.bearish_disable_fallback:
            return []
        result = []
        for sig in signals:
            p = sig.market_data.periods
            sk = _src_key(sig.source)
            is_dex = sk in ("dex", "dexscreener")
            if sig.phase == SignalPhase.REJECT: continue
            if sig.score.total_score < s.fallback_min_score: continue
            # V3.1.1: 杠杆/DEX/非CEX全部禁止进fallback
            if _is_leverage_symbol(sig.symbol) and _base_symbol(sig.symbol) not in s.leverage_allowlist:
                if s.leverage_token_full_block: continue
            if is_dex and s.dex_fallback_disabled: continue
            if getattr(s, 'fallback_require_cex_only', True) and sk not in CEX_SOURCE_NAMES: continue
            if p.turnover_24h > 0 and p.turnover_24h < s.fallback_min_turnover_24h: continue
            if p.turnover_1h > 0 and p.turnover_1h < s.fallback_min_turnover_1h: continue
            if p.change_1h <= 0: continue
            if p.change_4h < 0: continue
            if p.change_24h > s.fallback_max_24h_change: continue
            # 位置门槛
            pos = getattr(p, 'position_in_24h_range', 0.5)
            if s.fallback_reject_high_position and pos >= s.fallback_max_position: continue
            # 慢修复/弱修复
            if s.fallback_reject_slow_repair and p.change_24h < 0:
                if p.change_1h < s.slow_repair_min_1h or p.change_4h < s.slow_repair_min_4h: continue
            if (0 < p.change_1h <= s.weak_repair_max_1h and p.change_4h < s.weak_repair_max_4h
                and p.volume_ratio_5m < s.weak_repair_min_vr5m and p.change_24h < 0): continue
            # 无量修复
            if p.change_24h < 0 and p.volume_ratio_5m < 1.3 and p.volume_ratio_15m < 1.2: continue
            # 纯脉冲
            if p.change_5m > s.pulse_5m_change_threshold and p.change_1h < s.pulse_5m_1h_max: continue
            if p.change_15m > 6 and p.change_1h < 3 and p.change_4h < 2: continue
            # 尾段
            if p.change_24h > s.tail_surge_24h_min and p.change_4h < 2.0: continue
            # 量比异常
            if p.volume_ratio_5m >= s.volume_ratio_heavy_threshold and p.change_15m < 1.5: continue
            if p.volume_ratio_5m > 5 and p.volume_ratio_15m < 1.3: continue
            # 高位反抽
            pos = getattr(p, 'position_in_24h_range', 0.5)
            if pos >= 0.65 and p.change_24h > 5 and p.change_4h < 2: continue
            # 小所小币
            if s.fallback_reject_small_exchange:
                if sk in ("gate", "bybit", "dex", "dexscreener") and p.turnover_24h < 3_000_000: continue
            result.append(sig)
        return result

    # =================== 高质量少量候选最终闸门 (V3.1) ===================
    def high_quality_final_gate(self, signals: list[Signal], day_strength: str = "normal") -> list[Signal]:
        """V3.1: 高质量少量候选筛选器
        - 正常最多2个, 强势日最多3个, 弱市最多0~1个
        - 每个候选必须是clear candidate, 凑数的不推
        - 允许返回空列表
        """
        s = self.settings
        if not s.high_quality_mode or not signals:
            return signals

        # 根据 day_strength 决定本轮最大推送数
        if day_strength == "strong":
            max_push = s.strong_day_max_main_push
        elif day_strength == "weak":
            max_push = 1
        else:
            max_push = s.default_max_main_push

        # 大盘bearish进一步收紧
        regime = self._market_regime
        if regime == "bearish":
            max_push = min(max_push, 1)
        elif regime == "weak_neutral":
            max_push = min(max_push, 2)

        # 多因子排序: 不只看分数
        def _final_key(sig: Signal) -> tuple:
            p = sig.market_data.periods
            sk_local = _src_key(sig.source)
            suitable = 1 if sig.advice.is_suitable_for_24h_trade else 0
            pos = getattr(p, 'position_in_24h_range', 0.5)
            low_pos = 1 if pos < 0.55 else 0
            cex = 1 if sk_local in CEX_SOURCE_NAMES else 0
            not_lev = 0 if _is_leverage_symbol(sig.symbol) else 1
            not_dex = 0 if sk_local in ("dex", "dexscreener") else 1
            coordinated = 1 if (2 < p.change_1h < 15 and 1 < p.change_4h < 20) else 0
            not_tail = 1 if not (p.change_24h > 10 and p.change_4h < 2) else 0
            not_slow = 1 if not (p.change_24h < 0 and p.change_1h < 3) else 0
            not_pulse = 1 if not (p.change_5m > 5 and p.change_15m < 1.5) else 0
            liq = 1 if p.turnover_24h > 2_000_000 else 0
            top_ex = 1 if sk_local in ("binance", "okx", "bitget") else 0
            return (suitable, cex, not_lev, not_dex, low_pos, coordinated, not_tail, not_slow, not_pulse, liq, top_ex, sig.score.total_score)

        signals.sort(key=_final_key, reverse=True)

        # 逐个检查, 只保留真正的clear candidate
        result: list[Signal] = []
        for i, sig in enumerate(signals):
            if len(result) >= max_push:
                break

            p = sig.market_data.periods
            sc = sig.score.total_score

            # 分数不够 → 后面的更不行
            if sc < s.high_quality_min_score:
                logger.info(f"HQGate: #{i+1} {sig.symbol} score={sc:.0f} < {s.high_quality_min_score:.0f}, 放弃后续")
                break

            # require_clear_candidate: 结构有硬伤不推
            if s.require_clear_candidate:
                # 慢修复不配
                if p.change_24h < 0 and p.change_1h < s.slow_repair_min_1h:
                    logger.info(f"HQGate: #{i+1} {sig.symbol} 慢修复跳过")
                    continue
                # 尾段不配
                if p.change_24h > s.tail_surge_24h_min and p.change_4h < 2:
                    logger.info(f"HQGate: #{i+1} {sig.symbol} 尾段跳过")
                    continue
                # 纯脉冲不配
                if p.change_5m > 5 and p.change_15m < 1.5:
                    logger.info(f"HQGate: #{i+1} {sig.symbol} 纯脉冲跳过")
                    continue

            # 与已选候选有差距 → 是否凑数?
            if result and s.high_quality_min_edge > 0:
                gap = result[-1].score.total_score - sc
                if gap >= s.high_quality_min_edge:
                    pass  # 差距明显, 这个本身够强就收
                elif sc < 72:
                    # 差距不大且不够强 → 凑数不推
                    logger.info(f"HQGate: #{i+1} {sig.symbol} gap={gap:.0f} too close & score={sc:.0f}<72, 凑数不推")
                    continue

            result.append(sig)

        if len(result) < len(signals):
            logger.info(f"HQGate: {len(signals)} candidates → {len(result)} (day={day_strength} max={max_push})")

        return result

    # 向后兼容: 旧名指向新gate
    def single_best_final_gate(self, signals: list[Signal]) -> list[Signal]:
        return self.high_quality_final_gate(signals, "normal")

    # =================== 跨交易所去重 (V2.9) ===================
    def cross_exchange_dedup(self, signals: list[Signal]) -> tuple[list[Signal], list[Signal]]:
        """同币多交易所只保留最优版本，其余降入观察池。
        返回 (保留的主推, 降级到观察池的)
        """
        if not self.settings.cross_exchange_dedup_enabled:
            return signals, []

        by_base: dict[str, list[Signal]] = defaultdict(list)
        for sig in signals:
            base = _base_symbol(sig.symbol)
            by_base[base].append(sig)

        kept: list[Signal] = []
        demoted: list[Signal] = []
        for base, group in by_base.items():
            if len(group) == 1:
                kept.append(group[0])
                continue
            # 多交易所同币: 按(评分, 24h成交额)取最优
            group.sort(key=lambda s: (
                s.score.total_score,
                s.market_data.periods.turnover_24h,
            ), reverse=True)
            kept.append(group[0])
            demoted.extend(group[1:])
            if len(group) > 1:
                logger.info(f"CrossDedup: {base} 保留 {group[0].source}({group[0].score.total_score:.0f}), "
                            f"降级 {[s.source for s in group[1:]]}")

        return kept, demoted

    # =================== 叙事去重 ===================
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
