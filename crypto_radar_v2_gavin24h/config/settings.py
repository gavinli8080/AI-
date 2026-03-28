
"""
Crypto Radar V2 - 配置 (Gavin 24h 定制版)
偏保守，适配“纸飞机初筛 -> 多AI二筛 -> 24小时短线执行”流程。
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

def _e(k, d=""): return os.getenv(k, d)
def _ei(k, d=0): return int(os.getenv(k, str(d)))
def _ef(k, d=0.0): return float(os.getenv(k, str(d)))
def _eb(k, d=False): return os.getenv(k, str(d)).lower() in ("1","true","yes")
def _el(k, d=""):
    v = os.getenv(k, d)
    return [x.strip() for x in v.split(",") if x.strip()] if v else []

@dataclass
class ExchangeConfig:
    enabled: bool = False
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    max_pairs: int = 200
    rate_limit_ms: int = 100

@dataclass
class Settings:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    binance: ExchangeConfig = field(default_factory=ExchangeConfig)
    okx: ExchangeConfig = field(default_factory=ExchangeConfig)
    bitget: ExchangeConfig = field(default_factory=ExchangeConfig)
    gate: ExchangeConfig = field(default_factory=ExchangeConfig)
    bybit: ExchangeConfig = field(default_factory=ExchangeConfig)
    dexscreener_enabled: bool = True

    scan_interval_seconds: int = 60
    scoring_mode: str = "strict"

    min_24h_turnover: float = 600_000
    min_1h_turnover: float = 80_000
    max_price_change_1h: float = 35.0
    min_market_cap: float = 0

    dex_min_liquidity: float = 80_000
    dex_min_pool_age_hours: float = 48
    dex_min_txns_1h: int = 80
    dex_chains: list[str] = field(default_factory=lambda: ["solana","base","ethereum","bsc"])

    max_daily_pushes: int = 24
    cluster_max_per_group: int = 2
    low_quality_fuse_threshold: int = 4

    main_push_cooldown: int = 1800
    watchlist_cooldown: int = 900
    same_symbol_cooldown_seconds: int = 1800
    cooldown_phase_change_reset: bool = True
    cooldown_price_change_reset_pct: float = 2.0
    cooldown_score_jump_reset: float = 10.0

    watchlist_enabled: bool = True
    watchlist_min_score: float = 28.0
    max_daily_watchlist: int = 20
    watchlist_min_24h_turnover_mult: float = 0.6
    watchlist_fine_change_mult: float = 0.75

    adaptive_enabled: bool = True
    adaptive_lookback_cycles: int = 20
    adaptive_min_signals_threshold: int = 2
    adaptive_watchlist_score_relax: float = 4.0
    adaptive_fine_change_relax: float = 0.85

    volume_ratio_warning_threshold: float = 12.0
    volume_ratio_heavy_threshold: float = 20.0
    volume_ratio_reject_threshold: float = 35.0
    volume_ratio_extreme_threshold: float = 50.0

    push_scan_stats: bool = False
    push_watchlist_on_empty_main: bool = True
    empty_main_watchlist_count: int = 4

    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)

    push_min_score: float = 50.0
    grade_a_threshold: float = 75.0
    grade_b_threshold: float = 55.0

    db_path: str = "data/radar.db"
    log_level: str = "INFO"
    log_file: str = "data/radar.log"
    http_timeout: int = 15
    http_retries: int = 3
    proxy: str = ""

    price_deviation_threshold: float = 2.5
    market_cache_ttl: int = 1800
    enable_price_verification: bool = True

    # 24h 短线定制
    main_require_positive_1h: bool = True
    main_require_positive_4h: bool = True
    main_max_24h_change: float = 18.0
    main_max_1h_change: float = 8.5
    main_min_turnover_24h: float = 1_000_000
    main_min_turnover_1h: float = 120_000
    leverage_watchlist_only: bool = True
    leverage_allowlist: list[str] = field(default_factory=list)
    watchlist_reject_if_24h_overheat: float = 28.0

    # V2.9: 推送前复核
    recheck_enabled: bool = True
    recheck_max_deviation_pct: float = 3.0   # 主推复核偏差超此值→降级
    wl_recheck_max_deviation_pct: float = 2.5  # 观察池复核偏差超此值→丢弃

    # V2.9: 跨交易所去重
    cross_exchange_dedup_enabled: bool = True

    # V2.9: 加强脉冲/假突破过滤
    pulse_5m_change_threshold: float = 5.0   # 5m涨>此值且1h<2%→纯脉冲拒绝
    pulse_5m_1h_max: float = 2.0
    fake_breakout_5m_min: float = 3.0        # 5m涨>此值且4h<0→假突破降级
    tail_surge_24h_min: float = 10.0         # 24h>此值且4h<2%→尾段惩罚

    # V2.9.1: 24h区间位置
    high_position_reject_threshold: float = 0.92  # 区间位置>此值且非强突破→拒绝主推
    high_position_demote_threshold: float = 0.85  # 区间位置>此值→降低评分

    # V2.9.1: 弱修复/死猫跳过滤
    weak_repair_max_1h: float = 2.0    # 24h<0 且 1h<此值→弱修复
    weak_repair_max_4h: float = 1.5    # 24h<0 且 4h<此值→弱修复
    weak_repair_min_vr5m: float = 1.8  # 弱修复但量比<此值→无量修复

    # V2.9.1: 观察池收紧
    watchlist_min_score_strict: float = 35.0   # 严格观察池最低分(替代28)
    watchlist_require_positive_1h: bool = True  # 观察池也要求1h>0
    max_daily_watchlist_strict: int = 8        # 每日观察池上限收紧

    # V2.9.1: 大盘环境
    market_regime_enabled: bool = True
    market_regime_bearish_push_min_score: float = 65.0  # 大盘弱时主推最低分提高
    market_regime_bearish_wl_min_score: float = 45.0    # 大盘弱时观察池最低分提高

    # V2.9.1: 唯一一单模式
    single_best_mode: bool = True       # 每轮主推最多保留N个
    single_best_max_push: int = 1       # 最多推几个主推 (V2.9.2: 1)
    single_best_min_score: float = 58.0 # 主推最低可接受分数 (V2.9.2: 58)
    single_best_confidence_gap: float = 8.0  # V2.9.2: top1和top2差距<此值→不够确定,不推
    single_best_weak_threshold: float = 62.0 # V2.9.2: top1低于此值视为"勉强可做",不推

    # V2.9.1: 量比一致性
    vol_ratio_inconsistency_penalty: bool = True  # 5m高但15m/1h不跟→额外惩罚

    # V2.9.2: 允许空轮 — 主推和观察池都不够时整轮静默
    strict_empty_round_allowed: bool = True
    # V2.9.2: fallback最低分门槛
    fallback_min_score: float = 42.0
    fallback_min_turnover_24h: float = 800_000

    # V2.9.2: 大盘bearish时小所降级
    market_regime_bearish_demote_minor_sources: bool = True  # bearish时gate/bybit/dex小币降级
    market_regime_neutral_min_turnover_24h: float = 1_500_000  # neutral时主推最低24h额

    # V2.9.2: 观察池更少更精
    max_daily_watchlist_strict: int = 5        # V2.9.2: 5条
    watchlist_min_turnover_24h: float = 500_000  # 观察池最低24h额


def load_settings() -> Settings:
    s = Settings()
    s.telegram_bot_token = _e("TELEGRAM_BOT_TOKEN")
    s.telegram_chat_id = _e("TELEGRAM_CHAT_ID")
    s.binance = ExchangeConfig(enabled=_eb("BINANCE_ENABLED",True), api_key=_e("BINANCE_API_KEY"), api_secret=_e("BINANCE_API_SECRET"), max_pairs=_ei("BINANCE_MAX_PAIRS",200))
    s.okx = ExchangeConfig(enabled=_eb("OKX_ENABLED",True), api_key=_e("OKX_API_KEY"), api_secret=_e("OKX_API_SECRET"), passphrase=_e("OKX_PASSPHRASE"), max_pairs=_ei("OKX_MAX_PAIRS",200))
    s.bitget = ExchangeConfig(enabled=_eb("BITGET_ENABLED",True), api_key=_e("BITGET_API_KEY"), api_secret=_e("BITGET_API_SECRET"), passphrase=_e("BITGET_PASSPHRASE"), max_pairs=_ei("BITGET_MAX_PAIRS",200))
    s.gate = ExchangeConfig(enabled=_eb("GATE_ENABLED",True), api_key=_e("GATE_API_KEY"), api_secret=_e("GATE_API_SECRET"), max_pairs=_ei("GATE_MAX_PAIRS",200))
    s.bybit = ExchangeConfig(enabled=_eb("BYBIT_ENABLED",True), api_key=_e("BYBIT_API_KEY"), api_secret=_e("BYBIT_API_SECRET"), max_pairs=_ei("BYBIT_MAX_PAIRS",200))
    s.dexscreener_enabled = _eb("DEXSCREENER_ENABLED",True)
    s.dex_chains = _el("DEX_CHAINS","solana,base,ethereum,bsc")
    s.scan_interval_seconds = _ei("SCAN_INTERVAL",60)
    s.scoring_mode = _e("SCORING_MODE","strict")
    s.min_24h_turnover = _ef("MIN_24H_TURNOVER",600_000)
    s.min_1h_turnover = _ef("MIN_1H_TURNOVER",80_000)
    s.max_price_change_1h = _ef("MAX_CHANGE_1H",35.0)
    s.dex_min_liquidity = _ef("DEX_MIN_LIQUIDITY",80_000)
    s.dex_min_pool_age_hours = _ef("DEX_MIN_POOL_AGE_HOURS",48)
    s.dex_min_txns_1h = _ei("DEX_MIN_TXNS_1H",80)
    s.max_daily_pushes = _ei("MAX_DAILY_PUSHES",24)
    s.main_push_cooldown = _ei("MAIN_PUSH_COOLDOWN",1800)
    s.watchlist_cooldown = _ei("WATCHLIST_COOLDOWN",900)
    s.cooldown_phase_change_reset = _eb("COOLDOWN_PHASE_RESET",True)
    s.cooldown_price_change_reset_pct = _ef("COOLDOWN_PRICE_RESET_PCT",2.0)
    s.cooldown_score_jump_reset = _ef("COOLDOWN_SCORE_JUMP_RESET",10.0)
    s.watchlist_enabled = _eb("WATCHLIST_ENABLED",True)
    s.watchlist_min_score = _ef("WATCHLIST_MIN_SCORE",28.0)
    s.max_daily_watchlist = _ei("MAX_DAILY_WATCHLIST",20)
    s.watchlist_min_24h_turnover_mult = _ef("WATCHLIST_TURNOVER_MULT",0.6)
    s.watchlist_fine_change_mult = _ef("WATCHLIST_FINE_CHANGE_MULT",0.75)
    s.adaptive_enabled = _eb("ADAPTIVE_ENABLED",True)
    s.adaptive_lookback_cycles = _ei("ADAPTIVE_LOOKBACK_CYCLES",20)
    s.adaptive_min_signals_threshold = _ei("ADAPTIVE_MIN_SIGNALS",2)
    s.adaptive_watchlist_score_relax = _ef("ADAPTIVE_WATCHLIST_RELAX",4.0)
    s.adaptive_fine_change_relax = _ef("ADAPTIVE_FINE_CHANGE_RELAX",0.85)
    s.volume_ratio_warning_threshold = _ef("VOL_RATIO_WARNING",12.0)
    s.volume_ratio_heavy_threshold = _ef("VOL_RATIO_HEAVY",20.0)
    s.volume_ratio_reject_threshold = _ef("VOL_RATIO_REJECT",35.0)
    s.volume_ratio_extreme_threshold = _ef("VOL_RATIO_EXTREME",50.0)
    s.push_watchlist_on_empty_main = _eb("PUSH_WATCHLIST_ON_EMPTY",True)
    s.empty_main_watchlist_count = _ei("EMPTY_MAIN_WL_COUNT",4)
    s.whitelist = _el("WHITELIST","BTC,ETH,TAO,RNDR,NEAR,FET,VIRTUAL,AIXBT")
    s.blacklist = _el("BLACKLIST")
    s.push_min_score = _ef("PUSH_MIN_SCORE",50.0)
    s.db_path = _e("DB_PATH","data/radar.db")
    s.log_level = _e("LOG_LEVEL","INFO")
    s.log_file = _e("LOG_FILE","data/radar.log")
    s.http_timeout = _ei("HTTP_TIMEOUT",15)
    s.http_retries = _ei("HTTP_RETRIES",3)
    s.proxy = _e("PROXY_URL", _e("PROXY", ""))
    s.price_deviation_threshold = _ef("PRICE_DEVIATION_THRESHOLD",2.5)
    s.market_cache_ttl = _ei("MARKET_CACHE_TTL",1800)
    s.enable_price_verification = _eb("ENABLE_PRICE_VERIFICATION",True)
    s.main_require_positive_1h = _eb("MAIN_REQUIRE_POSITIVE_1H",True)
    s.main_require_positive_4h = _eb("MAIN_REQUIRE_POSITIVE_4H",True)
    s.main_max_24h_change = _ef("MAIN_MAX_24H_CHANGE",18.0)
    s.main_max_1h_change = _ef("MAIN_MAX_1H_CHANGE",8.5)
    s.main_min_turnover_24h = _ef("MAIN_MIN_TURNOVER_24H",1_000_000)
    s.main_min_turnover_1h = _ef("MAIN_MIN_TURNOVER_1H",120_000)
    s.leverage_watchlist_only = _eb("LEVERAGE_WATCHLIST_ONLY",True)
    s.leverage_allowlist = [x.upper() for x in _el("LEVERAGE_ALLOWLIST")]
    s.watchlist_reject_if_24h_overheat = _ef("WATCHLIST_REJECT_IF_24H_OVERHEAT",28.0)
    # V2.9
    s.recheck_enabled = _eb("RECHECK_ENABLED", True)
    s.recheck_max_deviation_pct = _ef("RECHECK_MAX_DEVIATION_PCT", 3.0)
    s.cross_exchange_dedup_enabled = _eb("CROSS_EXCHANGE_DEDUP_ENABLED", True)
    s.pulse_5m_change_threshold = _ef("PULSE_5M_CHANGE_THRESHOLD", 5.0)
    s.pulse_5m_1h_max = _ef("PULSE_5M_1H_MAX", 2.0)
    s.fake_breakout_5m_min = _ef("FAKE_BREAKOUT_5M_MIN", 3.0)
    s.tail_surge_24h_min = _ef("TAIL_SURGE_24H_MIN", 10.0)
    # V2.9.1
    s.high_position_reject_threshold = _ef("HIGH_POSITION_REJECT_THRESHOLD", 0.92)
    s.high_position_demote_threshold = _ef("HIGH_POSITION_DEMOTE_THRESHOLD", 0.85)
    s.weak_repair_max_1h = _ef("WEAK_REPAIR_MAX_1H", 2.0)
    s.weak_repair_max_4h = _ef("WEAK_REPAIR_MAX_4H", 1.5)
    s.weak_repair_min_vr5m = _ef("WEAK_REPAIR_MIN_VR5M", 1.8)
    s.watchlist_min_score_strict = _ef("WATCHLIST_MIN_SCORE_STRICT", 35.0)
    s.watchlist_require_positive_1h = _eb("WATCHLIST_REQUIRE_POSITIVE_1H", True)
    s.max_daily_watchlist_strict = _ei("MAX_DAILY_WATCHLIST_STRICT", 8)
    s.market_regime_enabled = _eb("MARKET_REGIME_ENABLED", True)
    s.market_regime_bearish_push_min_score = _ef("MARKET_REGIME_BEARISH_PUSH_MIN_SCORE", 65.0)
    s.market_regime_bearish_wl_min_score = _ef("MARKET_REGIME_BEARISH_WL_MIN_SCORE", 45.0)
    s.single_best_mode = _eb("SINGLE_BEST_MODE", True)
    s.single_best_max_push = _ei("SINGLE_BEST_MAX_PUSH", 2)
    s.single_best_min_score = _ef("SINGLE_BEST_MIN_SCORE", 55.0)
    s.vol_ratio_inconsistency_penalty = _eb("VOL_RATIO_INCONSISTENCY_PENALTY", True)
    s.wl_recheck_max_deviation_pct = _ef("WL_RECHECK_MAX_DEVIATION_PCT", 2.5)
    # V2.9.2
    s.single_best_confidence_gap = _ef("SINGLE_BEST_CONFIDENCE_GAP", 8.0)
    s.single_best_weak_threshold = _ef("SINGLE_BEST_WEAK_THRESHOLD", 62.0)
    s.strict_empty_round_allowed = _eb("STRICT_EMPTY_ROUND_ALLOWED", True)
    s.fallback_min_score = _ef("FALLBACK_MIN_SCORE", 42.0)
    s.fallback_min_turnover_24h = _ef("FALLBACK_MIN_TURNOVER_24H", 800_000)
    s.market_regime_bearish_demote_minor_sources = _eb("MARKET_REGIME_BEARISH_DEMOTE_MINOR_SOURCES", True)
    s.market_regime_neutral_min_turnover_24h = _ef("MARKET_REGIME_NEUTRAL_MIN_TURNOVER_24H", 1_500_000)
    s.watchlist_min_turnover_24h = _ef("WATCHLIST_MIN_TURNOVER_24H", 500_000)
    return s
