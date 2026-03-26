"""
Crypto Radar V2 - 核心数据模型 (V2.5)
新增: 交易标的合法性 + 价格源一致性字段
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
import time


class SignalPhase(str, Enum):
    STARTUP = "startup"
    ACCELERATION = "acceleration"
    OVERHEATED = "overheated"
    WATCH = "watch"
    REJECT = "reject"

    @property
    def cn(self) -> str:
        return {
            "startup": "启动", "acceleration": "加速",
            "overheated": "过热", "watch": "观察", "reject": "放弃",
        }[self.value]


class ChaseRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def cn(self) -> str:
        return {"low": "低", "medium": "中", "high": "高"}[self.value]


class PositionSize(str, Enum):
    IGNORE = "ignore"
    LIGHT = "light"
    MEDIUM = "medium"


class HoldingWindow(str, Enum):
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    H24 = "24h"


class ScoreGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class MarketStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


class SignalSourceType(str, Enum):
    CEX_SPOT = "CEX_SPOT"
    DEX = "DEX"
    UNKNOWN = "UNKNOWN"


@dataclass
class MultiPeriodData:
    change_1m: float = 0.0
    change_5m: float = 0.0
    change_15m: float = 0.0
    change_1h: float = 0.0
    change_4h: float = 0.0
    change_24h: float = 0.0

    volume_1m: float = 0.0
    volume_5m: float = 0.0
    volume_15m: float = 0.0
    volume_1h: float = 0.0
    volume_4h: float = 0.0
    volume_24h: float = 0.0

    volume_ratio_5m: float = 1.0
    volume_ratio_15m: float = 1.0
    volume_ratio_1h: float = 1.0

    turnover_5m: float = 0.0
    turnover_15m: float = 0.0
    turnover_1h: float = 0.0
    turnover_24h: float = 0.0

    trades_5m: int = 0
    trades_15m: int = 0
    trades_1h: int = 0
    trades_24h: int = 0


@dataclass
class LiquidityData:
    bid_ask_spread_pct: float = 0.0
    depth_score: float = 0.0
    volume_to_mcap_ratio: float = 0.0
    suspected_wash_trading: bool = False


@dataclass
class ChainData:
    pool_age_hours: float = 0.0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    fdv: float = 0.0
    txns_5m: int = 0
    txns_1h: int = 0
    buys_vs_sells_ratio: float = 1.0
    top_holders_pct: float = 0.0
    buy_tax: float = 0.0
    sell_tax: float = 0.0
    honeypot_flag: bool = False
    lp_locked_flag: bool = False
    mintable_flag: bool = False
    blacklist_flag: bool = False
    pause_flag: bool = False
    contract_risk_tags: list[str] = field(default_factory=list)
    chain: str = ""
    pair_url: str = ""


@dataclass
class ChaseRiskDetail:
    chase_risk_level: ChaseRiskLevel = ChaseRiskLevel.LOW
    is_chase_high_danger: bool = False
    should_wait_pullback: bool = False
    instant_risk: bool = False
    structural_risk: bool = False
    near_high_risk: bool = False
    volume_exhaust_risk: bool = False
    second_wave_end_risk: bool = False
    fake_breakout_risk: bool = False


@dataclass
class ScoreBreakdown:
    momentum_score: float = 0.0
    volume_quality_score: float = 0.0
    liquidity_score: float = 0.0
    continuation_score: float = 0.0
    breakout_quality_score: float = 0.0
    overheat_penalty: float = 0.0
    manipulation_risk_penalty: float = 0.0
    chain_risk_penalty: float = 0.0
    execution_score: float = 0.0

    total_score: float = 0.0
    grade: ScoreGrade = ScoreGrade.C

    score_reasons: list[str] = field(default_factory=list)
    penalty_reasons: list[str] = field(default_factory=list)
    watch_reasons: list[str] = field(default_factory=list)
    reject_reasons: list[str] = field(default_factory=list)


@dataclass
class TradingAdvice:
    is_suitable_for_24h_trade: bool = False
    recommended_holding_window: HoldingWindow = HoldingWindow.H1
    suggested_position_size: PositionSize = PositionSize.IGNORE
    execution_priority_score: float = 0.0
    rejection_reason: str = ""
    suggested_action: str = "放弃"
    not_recommended_if_chasing: bool = True
    invalidation_hint: str = ""
    take_profit_hint: str = ""
    stop_loss_hint: str = ""
    is_second_wave_opportunity: bool = False
    is_near_breakout: bool = False


@dataclass
class MarketValidation:
    """交易标的合法性与价格源校验结果"""
    market_status: MarketStatus = MarketStatus.UNKNOWN
    signal_source_type: SignalSourceType = SignalSourceType.UNKNOWN
    symbol_validated: bool = False
    price_verified: bool = False
    market_id_raw: str = ""            # 交易所原始 symbol/instId
    source_validation_reason: str = "" # 校验失败原因
    kline_close_price: float = 0.0     # K线验证价格
    price_deviation_pct: float = 0.0   # ticker vs kline 偏差百分比


@dataclass
class MarketData:
    symbol: str = ""
    source: str = ""
    price: float = 0.0
    timestamp: float = field(default_factory=time.time)

    periods: MultiPeriodData = field(default_factory=MultiPeriodData)
    liquidity: LiquidityData = field(default_factory=LiquidityData)
    chain_data: Optional[ChainData] = None

    market_cap: float = 0.0
    volatility_24h: float = 0.0

    narrative_tag: str = ""
    sector_tag: str = ""
    chain_tag: str = ""
    style_tag: str = ""

    trade_url: str = ""

    # V2.5: 合法性校验
    validation: MarketValidation = field(default_factory=MarketValidation)


@dataclass
class Signal:
    id: str = ""
    symbol: str = ""
    source: str = ""
    price: float = 0.0
    timestamp: float = field(default_factory=time.time)

    market_data: MarketData = field(default_factory=MarketData)

    phase: SignalPhase = SignalPhase.WATCH
    chase_risk: ChaseRiskDetail = field(default_factory=ChaseRiskDetail)
    score: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    advice: TradingAdvice = field(default_factory=TradingAdvice)

    trigger_reasons: list[str] = field(default_factory=list)
    risk_warnings: list[str] = field(default_factory=list)

    narrative_tag: str = ""
    chain_tag: str = ""

    pushed: bool = False


@dataclass
class TrackingRecord:
    signal_id: str = ""
    symbol: str = ""
    source: str = ""
    entry_price: float = 0.0
    entry_time: float = 0.0
    phase_at_entry: str = ""
    grade_at_entry: str = ""
    score_at_entry: float = 0.0

    price_15m: float = 0.0
    price_1h: float = 0.0
    price_4h: float = 0.0
    price_24h: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    max_gain_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    result_24h_pct: float = 0.0

    is_completed: bool = False
    completed_time: float = 0.0


@dataclass
class ActiveSymbolInfo:
    """交易所 active symbol 元数据缓存条目"""
    symbol: str = ""           # 标准化: BTC/USDT
    base_asset: str = ""       # BTC
    quote_asset: str = ""      # USDT
    raw_symbol: str = ""       # 交易所原始: BTCUSDT / BTC-USDT / BTC_USDT
    status: str = "active"     # active / inactive / delisted
    trading_enabled: bool = True
    market_type: str = "spot"
    exchange: str = ""
    trade_url: str = ""
