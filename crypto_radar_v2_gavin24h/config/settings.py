
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
    dexscreener_enabled: bool = False

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

    max_daily_pushes: int = 8
    cluster_max_per_group: int = 2
    low_quality_fuse_threshold: int = 4

    main_push_cooldown: int = 1800
    watchlist_cooldown: int = 900
    same_symbol_cooldown_seconds: int = 1800
    cooldown_phase_change_reset: bool = True
    cooldown_price_change_reset_pct: float = 2.0
    cooldown_score_jump_reset: float = 10.0

    watchlist_enabled: bool = True
    watchlist_min_score: float = 36.0
    max_daily_watchlist: int = 4
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
    push_watchlist_on_empty_main: bool = False
    empty_main_watchlist_count: int = 0

    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)

    push_min_score: float = 60.0
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
    main_min_turnover_24h: float = 2_000_000
    main_min_turnover_1h: float = 150_000
    leverage_allowlist: list[str] = field(default_factory=list)

    # V3.1.1: 杠杆代币全局禁推 (唯一开关, 替代旧的watchlist_only)
    leverage_token_full_block: bool = True       # true=杠杆代币彻底不推送(主推/观察/fallback全禁)

    # V3.1: DEX默认关闭推送
    dex_push_disabled: bool = True       # DEX不进主推
    dex_watchlist_disabled: bool = True  # DEX不进观察池
    dex_fallback_disabled: bool = True   # DEX不进fallback
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

    # V3.0: 24h区间位置硬门槛
    main_max_position: float = 0.58          # 主推最高允许24h区间位置
    high_position_reject_threshold: float = 0.92  # 评分惩罚:区间>此→重罚
    high_position_demote_threshold: float = 0.85  # 评分惩罚:区间>此→轻罚
    fallback_max_position: float = 0.52      # fallback最高允许位置
    watchlist_max_position: float = 0.65     # 观察池最高允许位置
    low_position_repair_bonus: bool = True   # 低位启动加分
    high_position_bounce_penalty: bool = True  # 高位反抽强惩罚

    # V3.0: 慢修复/弱修复/死猫跳 — 三级判定
    weak_repair_max_1h: float = 2.0    # 弱修复: 24h<0 且 1h<此值
    weak_repair_max_4h: float = 1.5    # 弱修复: 24h<0 且 4h<此值
    weak_repair_min_vr5m: float = 1.8  # 无量修复: 量比<此值
    slow_repair_min_1h: float = 3.0    # 慢修复: 24h<0 且 1h<此值(更宽)
    slow_repair_min_4h: float = 4.0    # 慢修复: 24h<0 且 4h<此值(更宽)
    slow_repair_min_turnover_1h: float = 100_000  # 慢修复: 1h额<此值

    # V3.0: 观察池 = 监控池,不是备选池
    watchlist_min_score_strict: float = 42.0   # 严格观察池最低分
    watchlist_require_positive_1h: bool = True
    max_daily_watchlist_strict: int = 2        # 每日观察池上限(严格)
    watchlist_require_cex_only: bool = True    # 观察池仅CEX
    watchlist_reject_small_exchange: bool = True  # 弱市小所不进观察池
    watchlist_min_turnover_24h: float = 500_000
    watchlist_reject_slow_repair: bool = True
    watchlist_reject_no_volume_repair: bool = True
    watchlist_reject_high_position_bounce: bool = True
    watchlist_require_real_continuation: bool = True  # 24h转正但1h/4h弱→不收

    # V3.0: 大盘环境 (4级: bullish/neutral/weak_neutral/bearish)
    market_regime_enabled: bool = True
    market_regime_bearish_push_min_score: float = 70.0
    market_regime_bearish_wl_min_score: float = 50.0
    bearish_disable_small_cap_main: bool = True    # bearish时小所小币不进主推
    bearish_disable_fallback: bool = True          # bearish时关闭fallback
    bearish_strict_watchlist: bool = True           # bearish时观察池大幅收紧
    weak_neutral_strict_main: bool = True           # weak_neutral时只允许高流动性主推
    weak_neutral_strict_watchlist: bool = True      # weak_neutral时观察池收紧
    weak_neutral_min_turnover_24h: float = 2_000_000  # weak_neutral主推最低24h额

    # V3.1: 高质量少量候选模式 (替代唯一一单)
    high_quality_mode: bool = True
    default_max_main_push: int = 2         # 正常最多2个主推
    strong_day_max_main_push: int = 3      # 强势日最多3个主推
    high_quality_min_score: float = 60.0   # 低于此分不推
    high_quality_min_edge: float = 5.0     # top-N和下一个差距<此→凑数不推
    require_clear_candidate: bool = True   # 要求明确候选才推
    require_cex_only_for_main: bool = True # 主推仅CEX现货

    # V3.0: 量比一致性 + 纯脉冲
    vol_ratio_inconsistency_penalty: bool = True
    pure_15m_pulse_reject: bool = True     # 15m暴涨但1h/4h不跟→拒绝

    # V3.0: 允许空轮
    strict_empty_round_allowed: bool = True

    # V3.1: fallback (几乎不用, 极高门槛)
    fallback_min_score: float = 60.0
    fallback_min_turnover_24h: float = 2_000_000
    fallback_min_turnover_1h: float = 150_000
    fallback_max_24h_change: float = 16.0
    fallback_reject_slow_repair: bool = True
    fallback_reject_high_position: bool = True
    fallback_reject_small_exchange: bool = True  # fallback不收小所小币
    fallback_require_cex_only: bool = True       # fallback仅CEX
    fallback_only_top_quality: bool = True        # fallback只收极高质量


def load_settings() -> Settings:
    s = Settings()
    s.telegram_bot_token = _e("TELEGRAM_BOT_TOKEN")
    s.telegram_chat_id = _e("TELEGRAM_CHAT_ID")
    s.binance = ExchangeConfig(enabled=_eb("BINANCE_ENABLED",True), api_key=_e("BINANCE_API_KEY"), api_secret=_e("BINANCE_API_SECRET"), max_pairs=_ei("BINANCE_MAX_PAIRS",200))
    s.okx = ExchangeConfig(enabled=_eb("OKX_ENABLED",True), api_key=_e("OKX_API_KEY"), api_secret=_e("OKX_API_SECRET"), passphrase=_e("OKX_PASSPHRASE"), max_pairs=_ei("OKX_MAX_PAIRS",200))
    s.bitget = ExchangeConfig(enabled=_eb("BITGET_ENABLED",True), api_key=_e("BITGET_API_KEY"), api_secret=_e("BITGET_API_SECRET"), passphrase=_e("BITGET_PASSPHRASE"), max_pairs=_ei("BITGET_MAX_PAIRS",200))
    s.gate = ExchangeConfig(enabled=_eb("GATE_ENABLED",True), api_key=_e("GATE_API_KEY"), api_secret=_e("GATE_API_SECRET"), max_pairs=_ei("GATE_MAX_PAIRS",200))
    s.bybit = ExchangeConfig(enabled=_eb("BYBIT_ENABLED",True), api_key=_e("BYBIT_API_KEY"), api_secret=_e("BYBIT_API_SECRET"), max_pairs=_ei("BYBIT_MAX_PAIRS",200))
    s.dexscreener_enabled = _eb("DEXSCREENER_ENABLED",False)
    s.dex_chains = _el("DEX_CHAINS","solana,base,ethereum,bsc")
    s.scan_interval_seconds = _ei("SCAN_INTERVAL",60)
    s.scoring_mode = _e("SCORING_MODE","strict")
    s.min_24h_turnover = _ef("MIN_24H_TURNOVER",600_000)
    s.min_1h_turnover = _ef("MIN_1H_TURNOVER",80_000)
    s.max_price_change_1h = _ef("MAX_CHANGE_1H",35.0)
    s.dex_min_liquidity = _ef("DEX_MIN_LIQUIDITY",80_000)
    s.dex_min_pool_age_hours = _ef("DEX_MIN_POOL_AGE_HOURS",48)
    s.dex_min_txns_1h = _ei("DEX_MIN_TXNS_1H",80)
    s.max_daily_pushes = _ei("MAX_DAILY_PUSHES",8)
    s.main_push_cooldown = _ei("MAIN_PUSH_COOLDOWN",1800)
    s.watchlist_cooldown = _ei("WATCHLIST_COOLDOWN",900)
    s.cooldown_phase_change_reset = _eb("COOLDOWN_PHASE_RESET",True)
    s.cooldown_price_change_reset_pct = _ef("COOLDOWN_PRICE_RESET_PCT",2.0)
    s.cooldown_score_jump_reset = _ef("COOLDOWN_SCORE_JUMP_RESET",10.0)
    s.watchlist_enabled = _eb("WATCHLIST_ENABLED",True)
    s.watchlist_min_score = _ef("WATCHLIST_MIN_SCORE",36.0)
    s.max_daily_watchlist = _ei("MAX_DAILY_WATCHLIST",4)
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
    s.push_watchlist_on_empty_main = _eb("PUSH_WATCHLIST_ON_EMPTY",False)
    s.empty_main_watchlist_count = _ei("EMPTY_MAIN_WL_COUNT",0)
    s.whitelist = _el("WHITELIST","BTC,ETH,TAO,RNDR,NEAR,FET,VIRTUAL,AIXBT")
    s.blacklist = _el("BLACKLIST")
    s.push_min_score = _ef("PUSH_MIN_SCORE",60.0)
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
    s.main_min_turnover_24h = _ef("MAIN_MIN_TURNOVER_24H",2_000_000)
    s.main_min_turnover_1h = _ef("MAIN_MIN_TURNOVER_1H",150_000)
    s.leverage_allowlist = [x.upper() for x in _el("LEVERAGE_ALLOWLIST")]
    # V3.1.1: 杠杆代币全局禁推 (唯一开关)
    s.leverage_token_full_block = _eb("LEVERAGE_TOKEN_FULL_BLOCK", True)
    # V3.1: DEX禁推
    s.dex_push_disabled = _eb("DEX_PUSH_DISABLED", True)
    s.dex_watchlist_disabled = _eb("DEX_WATCHLIST_DISABLED", True)
    s.dex_fallback_disabled = _eb("DEX_FALLBACK_DISABLED", True)
    s.watchlist_reject_if_24h_overheat = _ef("WATCHLIST_REJECT_IF_24H_OVERHEAT",28.0)
    # V2.9
    s.recheck_enabled = _eb("RECHECK_ENABLED", True)
    s.recheck_max_deviation_pct = _ef("RECHECK_MAX_DEVIATION_PCT", 3.0)
    s.cross_exchange_dedup_enabled = _eb("CROSS_EXCHANGE_DEDUP_ENABLED", True)
    s.pulse_5m_change_threshold = _ef("PULSE_5M_CHANGE_THRESHOLD", 5.0)
    s.pulse_5m_1h_max = _ef("PULSE_5M_1H_MAX", 2.0)
    s.fake_breakout_5m_min = _ef("FAKE_BREAKOUT_5M_MIN", 3.0)
    s.tail_surge_24h_min = _ef("TAIL_SURGE_24H_MIN", 10.0)
    # V3.0
    s.main_max_position = _ef("MAIN_MAX_POSITION_IN_24H_RANGE", 0.58)
    s.high_position_reject_threshold = _ef("HIGH_POSITION_REJECT_THRESHOLD", 0.92)
    s.high_position_demote_threshold = _ef("HIGH_POSITION_DEMOTE_THRESHOLD", 0.85)
    s.fallback_max_position = _ef("FALLBACK_MAX_POSITION_IN_24H_RANGE", 0.52)
    s.watchlist_max_position = _ef("WATCHLIST_MAX_POSITION_IN_24H_RANGE", 0.65)
    s.low_position_repair_bonus = _eb("LOW_POSITION_REPAIR_BONUS_ENABLED", True)
    s.high_position_bounce_penalty = _eb("HIGH_POSITION_BOUNCE_PENALTY_ENABLED", True)
    s.weak_repair_max_1h = _ef("WEAK_REPAIR_MAX_1H", 2.0)
    s.weak_repair_max_4h = _ef("WEAK_REPAIR_MAX_4H", 1.5)
    s.weak_repair_min_vr5m = _ef("WEAK_REPAIR_MIN_VR5M", 1.8)
    s.slow_repair_min_1h = _ef("SLOW_REPAIR_MIN_1H_CHANGE", 3.0)
    s.slow_repair_min_4h = _ef("SLOW_REPAIR_MIN_4H_CHANGE", 4.0)
    s.slow_repair_min_turnover_1h = _ef("SLOW_REPAIR_MIN_TURNOVER_1H", 100_000)
    s.watchlist_min_score_strict = _ef("WATCHLIST_MIN_SCORE_STRICT", 42.0)
    s.watchlist_require_positive_1h = _eb("WATCHLIST_REQUIRE_POSITIVE_1H", True)
    s.max_daily_watchlist_strict = _ei("MAX_DAILY_WATCHLIST_STRICT", 2)
    s.watchlist_require_cex_only = _eb("WATCHLIST_REQUIRE_CEX_ONLY", True)
    s.watchlist_reject_small_exchange = _eb("WATCHLIST_REJECT_SMALL_EXCHANGE_NOISE", True)
    s.watchlist_min_turnover_24h = _ef("WATCHLIST_MIN_TURNOVER_24H", 500_000)
    s.watchlist_reject_slow_repair = _eb("WATCHLIST_REJECT_SLOW_REPAIR", True)
    s.watchlist_reject_no_volume_repair = _eb("WATCHLIST_REJECT_NO_VOLUME_REPAIR", True)
    s.watchlist_reject_high_position_bounce = _eb("WATCHLIST_REJECT_HIGH_POSITION_BOUNCE", True)
    s.watchlist_require_real_continuation = _eb("WATCHLIST_REQUIRE_REAL_CONTINUATION", True)
    s.market_regime_enabled = _eb("MARKET_REGIME_ENABLED", True)
    s.market_regime_bearish_push_min_score = _ef("MARKET_REGIME_BEARISH_PUSH_MIN_SCORE", 70.0)
    s.market_regime_bearish_wl_min_score = _ef("MARKET_REGIME_BEARISH_WL_MIN_SCORE", 50.0)
    s.bearish_disable_small_cap_main = _eb("BEARISH_DISABLE_SMALL_CAP_MAIN", True)
    s.bearish_disable_fallback = _eb("BEARISH_DISABLE_FALLBACK", True)
    s.bearish_strict_watchlist = _eb("BEARISH_STRICT_WATCHLIST", True)
    s.weak_neutral_strict_main = _eb("WEAK_NEUTRAL_STRICT_MAIN", True)
    s.weak_neutral_strict_watchlist = _eb("WEAK_NEUTRAL_STRICT_WATCHLIST", True)
    s.weak_neutral_min_turnover_24h = _ef("WEAK_NEUTRAL_MIN_TURNOVER_24H", 2_000_000)
    s.high_quality_mode = _eb("HIGH_QUALITY_MODE", True)
    s.default_max_main_push = _ei("DEFAULT_MAX_MAIN_PUSH", 2)
    s.strong_day_max_main_push = _ei("STRONG_DAY_MAX_MAIN_PUSH", 3)
    s.high_quality_min_score = _ef("HIGH_QUALITY_MIN_SCORE", 60.0)
    s.high_quality_min_edge = _ef("HIGH_QUALITY_MIN_EDGE", 5.0)
    s.require_clear_candidate = _eb("REQUIRE_CLEAR_CANDIDATE", True)
    s.require_cex_only_for_main = _eb("REQUIRE_CEX_ONLY_FOR_MAIN", True)
    s.vol_ratio_inconsistency_penalty = _eb("VOL_RATIO_INCONSISTENCY_PENALTY", True)
    s.pure_15m_pulse_reject = _eb("PURE_15M_PULSE_REJECT_MAIN", True)
    s.wl_recheck_max_deviation_pct = _ef("WL_RECHECK_MAX_DEVIATION_PCT", 2.5)
    s.strict_empty_round_allowed = _eb("STRICT_EMPTY_ROUND_ALLOWED", True)
    s.fallback_min_score = _ef("FALLBACK_MIN_SCORE", 60.0)
    s.fallback_min_turnover_24h = _ef("FALLBACK_MIN_TURNOVER_24H", 2_000_000)
    s.fallback_min_turnover_1h = _ef("FALLBACK_MIN_TURNOVER_1H", 150_000)
    s.fallback_max_24h_change = _ef("FALLBACK_MAX_24H_CHANGE", 16.0)
    s.fallback_reject_slow_repair = _eb("FALLBACK_REJECT_SLOW_REPAIR", True)
    s.fallback_reject_high_position = _eb("FALLBACK_REJECT_HIGH_POSITION", True)
    s.fallback_reject_small_exchange = _eb("FALLBACK_REJECT_SMALL_EXCHANGE_NOISE", True)
    s.fallback_require_cex_only = _eb("FALLBACK_REQUIRE_CEX_ONLY", True)
    s.fallback_only_top_quality = _eb("FALLBACK_ONLY_TOP_QUALITY", True)
    return s
