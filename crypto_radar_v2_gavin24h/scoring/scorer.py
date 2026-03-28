"""
Crypto Radar V2 - 评分引擎 (V2.9.1)
尾段冲刺+纯脉冲惩罚强化, 24h结构偏好, 量比多周期联合
V2.9.1: 24h区间位置因子, 弱修复惩罚, 量比不一致惩罚
"""
from __future__ import annotations
import hashlib, time, logging
from models import (
    MarketData, Signal, SignalPhase, ChaseRiskLevel, ChaseRiskDetail,
    ScoreBreakdown, ScoreGrade, TradingAdvice, PositionSize, HoldingWindow,
    ExecutionLevel,
)
from config.settings import Settings

logger = logging.getLogger("radar.scorer")

MODE_PARAMS = {
    "strict": {
        "instant_risk_1m":3.0,"instant_risk_5m":8.0,"structural_risk_15m":10.0,"structural_risk_1h":20.0,"severe_1h":30.0,
        "exhaust_vol_ratio":3.0,"exhaust_change_cap":1.0,"fake_change_min":3.0,"fake_vol_max":1.5,
        "second_wave_4h":15.0,"second_wave_1h":8.0,"second_wave_5m_cap":2.0,
        "danger_threshold":3,"medium_threshold":1,
        "phase_reject_1h":50.0,"phase_reject_15m":20.0,
        "phase_overheat_5m":10.0,"phase_overheat_15m":15.0,"phase_overheat_1h":25.0,
        "phase_accel_5m_lo":2.0,"phase_accel_5m_hi":10.0,"phase_accel_15m":3.0,"phase_accel_vol":2.0,
        "phase_startup_5m":1.5,"phase_startup_15m_cap":8.0,"phase_startup_1h_cap":15.0,"phase_startup_vol":1.5,
        "mom_5m_lo":1.0,"mom_5m_hi":8.0,"mom_15m_hi":15.0,"mom_1h_hi":25.0,
        "cont_5m_min":1.5,"cont_15m_cap":10.0,"cont_1h_cap":20.0,"cont_4h_cap":15.0,"cont_24h_cap":30.0,"cont_24h_bonus":10.0,
        "oh_5m":10.0,"oh_1h":25.0,"oh_15m":15.0,
        "grade_a":75.0,"grade_b":55.0,"act_start":60.0,"act_accel":70.0,"act_strong":80.0,
    },
    "balanced": {
        "instant_risk_1m":4.0,"instant_risk_5m":10.0,"structural_risk_15m":13.0,"structural_risk_1h":25.0,"severe_1h":40.0,
        "exhaust_vol_ratio":4.0,"exhaust_change_cap":1.5,"fake_change_min":4.0,"fake_vol_max":1.2,
        "second_wave_4h":20.0,"second_wave_1h":10.0,"second_wave_5m_cap":2.5,
        "danger_threshold":3,"medium_threshold":2,
        "phase_reject_1h":60.0,"phase_reject_15m":25.0,
        "phase_overheat_5m":12.0,"phase_overheat_15m":18.0,"phase_overheat_1h":30.0,
        "phase_accel_5m_lo":1.5,"phase_accel_5m_hi":12.0,"phase_accel_15m":2.5,"phase_accel_vol":1.8,
        "phase_startup_5m":1.0,"phase_startup_15m_cap":10.0,"phase_startup_1h_cap":20.0,"phase_startup_vol":1.3,
        "mom_5m_lo":0.8,"mom_5m_hi":10.0,"mom_15m_hi":18.0,"mom_1h_hi":30.0,
        "cont_5m_min":1.0,"cont_15m_cap":13.0,"cont_1h_cap":25.0,"cont_4h_cap":20.0,"cont_24h_cap":40.0,"cont_24h_bonus":15.0,
        "oh_5m":12.0,"oh_1h":30.0,"oh_15m":18.0,
        "grade_a":68.0,"grade_b":48.0,"act_start":50.0,"act_accel":60.0,"act_strong":72.0,
    },
    "calibration": {
        "instant_risk_1m":5.0,"instant_risk_5m":12.0,"structural_risk_15m":15.0,"structural_risk_1h":30.0,"severe_1h":50.0,
        "exhaust_vol_ratio":5.0,"exhaust_change_cap":2.0,"fake_change_min":5.0,"fake_vol_max":1.0,
        "second_wave_4h":25.0,"second_wave_1h":12.0,"second_wave_5m_cap":3.0,
        "danger_threshold":4,"medium_threshold":2,
        "phase_reject_1h":80.0,"phase_reject_15m":30.0,
        "phase_overheat_5m":15.0,"phase_overheat_15m":22.0,"phase_overheat_1h":40.0,
        "phase_accel_5m_lo":1.0,"phase_accel_5m_hi":15.0,"phase_accel_15m":2.0,"phase_accel_vol":1.5,
        "phase_startup_5m":0.8,"phase_startup_15m_cap":12.0,"phase_startup_1h_cap":25.0,"phase_startup_vol":1.2,
        "mom_5m_lo":0.5,"mom_5m_hi":12.0,"mom_15m_hi":22.0,"mom_1h_hi":35.0,
        "cont_5m_min":0.8,"cont_15m_cap":15.0,"cont_1h_cap":30.0,"cont_4h_cap":25.0,"cont_24h_cap":50.0,"cont_24h_bonus":20.0,
        "oh_5m":15.0,"oh_1h":35.0,"oh_15m":22.0,
        "grade_a":60.0,"grade_b":40.0,"act_start":40.0,"act_accel":50.0,"act_strong":62.0,
    },
}


class SignalScorer:
    def __init__(self, settings: Settings):
        self.settings = settings
        m = getattr(settings, "scoring_mode", "balanced")
        self.mode = m if m in MODE_PARAMS else "balanced"
        self.P = MODE_PARAMS[self.mode]

    def score(self, md: MarketData) -> Signal:
        sig = Signal(symbol=md.symbol, source=md.source, price=md.price,
                     timestamp=md.timestamp, market_data=md,
                     narrative_tag=md.narrative_tag, chain_tag=md.chain_tag)
        sig.chase_risk = self._chase(md)
        sig.phase = self._phase(md, sig.chase_risk)
        sig.score = self._calc(md, sig.chase_risk, sig.phase)
        sig.advice = self._advice(md, sig)
        sig.trigger_reasons = self._triggers(md, sig)
        sig.risk_warnings = self._warnings(md, sig)
        sig.id = hashlib.md5(f"{md.symbol}:{md.source}:{int(time.time()*1000)}".encode()).hexdigest()[:12]
        return sig

    def _chase(self, md: MarketData) -> ChaseRiskDetail:
        p, P = md.periods, self.P; r = ChaseRiskDetail(); dc = 0
        if p.change_1m > P["instant_risk_1m"] or p.change_5m > P["instant_risk_5m"]: r.instant_risk=True; dc+=1
        if p.change_15m > P["structural_risk_15m"] or p.change_1h > P["structural_risk_1h"]: r.structural_risk=True; dc+=1
        if p.change_1h > P["severe_1h"]: r.structural_risk=True; dc+=2
        if p.volume_ratio_5m > P["exhaust_vol_ratio"] and p.change_5m < P["exhaust_change_cap"]: r.volume_exhaust_risk=True; dc+=1
        if p.change_5m > P["fake_change_min"] and p.volume_ratio_5m < P["fake_vol_max"]: r.fake_breakout_risk=True; dc+=1
        if p.change_4h > P["second_wave_4h"] and p.change_1h > P["second_wave_1h"] and p.change_5m < P["second_wave_5m_cap"]: r.second_wave_end_risk=True; dc+=1
        if dc >= P["danger_threshold"]: r.chase_risk_level=ChaseRiskLevel.HIGH; r.is_chase_high_danger=True; r.should_wait_pullback=True
        elif dc >= P["medium_threshold"]: r.chase_risk_level=ChaseRiskLevel.MEDIUM; r.should_wait_pullback=r.structural_risk
        return r

    def _phase(self, md: MarketData, chase: ChaseRiskDetail) -> SignalPhase:
        p, P = md.periods, self.P
        if p.change_1h > P["phase_reject_1h"]: return SignalPhase.REJECT
        if chase.is_chase_high_danger and p.change_15m > P["phase_reject_15m"]: return SignalPhase.REJECT
        if p.change_5m > P["phase_overheat_5m"] and p.change_15m > P["phase_overheat_15m"]: return SignalPhase.OVERHEATED
        if p.change_1h > P["phase_overheat_1h"]: return SignalPhase.OVERHEATED
        if P["phase_accel_5m_lo"] < p.change_5m < P["phase_accel_5m_hi"] and p.change_15m > P["phase_accel_15m"] and p.volume_ratio_5m > P["phase_accel_vol"]:
            return SignalPhase.ACCELERATION
        if p.change_5m > P["phase_startup_5m"] and p.change_15m < P["phase_startup_15m_cap"] and p.change_1h < P["phase_startup_1h_cap"] and p.volume_ratio_5m > P["phase_startup_vol"]:
            return SignalPhase.STARTUP
        return SignalPhase.WATCH

    def _calc(self, md: MarketData, chase: ChaseRiskDetail, phase: SignalPhase) -> ScoreBreakdown:
        p, P, s = md.periods, self.P, self.settings
        sb = ScoreBreakdown()
        vrW = s.volume_ratio_warning_threshold
        vrH = s.volume_ratio_heavy_threshold

        # 动能 0-20
        m = 0.0
        if P["mom_5m_lo"] < p.change_5m < P["mom_5m_hi"]: m += min(p.change_5m*1.8,9); sb.score_reasons.append(f"5m涨{p.change_5m:.1f}%")
        if 2 < p.change_15m < P["mom_15m_hi"]: m += min(p.change_15m*0.6,6)
        if 2 < p.change_1h < P["mom_1h_hi"]: m += min(p.change_1h*0.25,5)
        sb.momentum_score = min(m, 20)

        # 量能 0-20
        v = 0.0
        if 1.8 < p.volume_ratio_5m <= vrW:
            v += min((p.volume_ratio_5m-1)*3,8); sb.score_reasons.append(f"5m量比{p.volume_ratio_5m:.1f}x")
        elif p.volume_ratio_5m > 1.8: v += 2
        if 1.3 < p.volume_ratio_15m < vrW: v += min((p.volume_ratio_15m-1)*2.5,6)
        if p.turnover_24h > 2e6: v+=3
        elif p.turnover_24h > 8e5: v+=2
        if p.turnover_1h > 1e5: v+=3
        elif p.turnover_1h > 4e4: v+=1.5
        sb.volume_quality_score = min(v, 20)

        # 流动性 0-15
        l = 0.0
        if p.turnover_24h > 5e6: l+=8
        elif p.turnover_24h > 1e6: l+=5
        elif p.turnover_24h > 5e5: l+=3
        elif p.turnover_24h > 2e5: l+=2
        if md.chain_data:
            if md.chain_data.liquidity_usd > 5e5: l+=5
            elif md.chain_data.liquidity_usd > 1e5: l+=3
            elif md.chain_data.liquidity_usd > 5e4: l+=1.5
        else: l+=2
        if md.liquidity.suspected_wash_trading: l=max(l-5,0); sb.penalty_reasons.append("疑似刷量")
        sb.liquidity_score = min(l, 15)

        # 延续性 0-15
        c = 0.0
        if p.change_5m > P["cont_5m_min"] and p.change_15m < P["cont_15m_cap"] and p.change_1h < P["cont_1h_cap"]:
            c+=5; sb.score_reasons.append("多周期协调")
        if 1.8 < p.volume_ratio_5m < vrW and p.volume_ratio_15m > 1.3: c+=3
        if p.change_4h < P["cont_4h_cap"] and p.change_24h < P["cont_24h_cap"]: c+=3
        if p.change_24h < P["cont_24h_bonus"]: c+=2; sb.score_reasons.append("24h有空间")
        # 1h/4h/24h协调加分
        if 2 < p.change_1h < 15 and 3 < p.change_4h < 20 and p.change_24h < 25:
            c+=3; sb.score_reasons.append("1h/4h/24h协调")
        # V3.0: 24h区间位置加分 — 低位+1h/4h转强=真正有空间
        pos = getattr(p, 'position_in_24h_range', 0.5)
        if s.low_position_repair_bonus and pos < 0.4:
            if p.change_1h > 2 and p.change_4h > 1:
                c += 3; sb.score_reasons.append(f"低位转强({pos:.0%})")
            else:
                c += 1; sb.score_reasons.append(f"低位({pos:.0%})")
        sb.continuation_score = min(c, 15)

        # 突破质量 0-10
        b = 0.0
        if p.change_5m > 1.5 and 2.5 < p.volume_ratio_5m < vrW: b+=5; sb.score_reasons.append("放量突破")
        if p.trades_5m > 80: b+=2
        elif p.trades_5m > 30: b+=1
        if p.change_1m > 0.8 and p.change_5m > 1.5: b+=3
        sb.breakout_quality_score = min(b, 10)

        # 过热惩罚 0-20
        oh = 0.0
        if p.change_5m > P["oh_5m"]: oh+=min((p.change_5m-P["oh_5m"])*1.8,8); sb.penalty_reasons.append(f"5m涨{p.change_5m:.1f}%过热")
        if p.change_1h > P["oh_1h"]: oh+=min((p.change_1h-P["oh_1h"])*0.4,6); sb.penalty_reasons.append(f"1h涨{p.change_1h:.1f}%过热")
        if p.change_15m > P["oh_15m"]: oh+=3
        if chase.is_chase_high_danger: oh+=3; sb.penalty_reasons.append("追高危险")
        # V2.8: 尾段冲刺强惩罚
        if p.change_1h > 15 and p.change_24h > 20 and p.change_5m > 3:
            oh += 5; sb.penalty_reasons.append("尾段冲刺(1h/24h已热+5m还冲)")
        # V2.9: 24h涨幅分布结构惩罚 — 涨幅集中在早期,近4h无延续空间
        ts_min = getattr(s, 'tail_surge_24h_min', 10.0)
        if p.change_24h > ts_min and p.change_4h < 2.0:
            oh += 4; sb.penalty_reasons.append(f"24h涨幅早期集中(4h仅{p.change_4h:.1f}%)")

        # V3.0: 24h区间位置惩罚 — 更严格
        pos = getattr(p, 'position_in_24h_range', 0.5)
        if pos >= s.high_position_reject_threshold:
            oh += 8; sb.penalty_reasons.append(f"24h区间顶部({pos:.0%})")
        elif pos >= s.high_position_demote_threshold:
            oh += 4; sb.penalty_reasons.append(f"24h区间偏高({pos:.0%})")
        elif pos >= 0.7:
            oh += 2; sb.penalty_reasons.append(f"24h偏上({pos:.0%})")

        # V3.0: 高位反抽 — 24h已热+位置偏高+4h弱
        if s.high_position_bounce_penalty and pos >= 0.65 and p.change_24h > 8 and p.change_4h < 3:
            oh += 5; sb.penalty_reasons.append(f"高位反抽(pos={pos:.0%} 24h={p.change_24h:.0f}%)")

        # V2.9.1: 弱修复/死猫跳惩罚 — 1h/4h涨幅微弱+量比不足=无力反弹
        if (0 < p.change_1h <= s.weak_repair_max_1h
            and p.change_4h < s.weak_repair_max_4h
            and p.volume_ratio_5m < s.weak_repair_min_vr5m
            and p.change_24h < 0):
            oh += 4; sb.penalty_reasons.append(f"弱修复(1h={p.change_1h:.1f}% vr={p.volume_ratio_5m:.1f}x 24h={p.change_24h:.1f}%)")

        sb.overheat_penalty = min(oh, 20)

        # 操纵风险 0-10
        mp = 0.0
        if md.liquidity.suspected_wash_trading: mp+=4
        # 四档量比+15m联合
        if p.volume_ratio_5m >= s.volume_ratio_extreme_threshold:
            mp+=8; sb.penalty_reasons.append(f"量比{p.volume_ratio_5m:.0f}x极端")
        elif p.volume_ratio_5m >= s.volume_ratio_reject_threshold:
            mp+=6; sb.penalty_reasons.append(f"量比{p.volume_ratio_5m:.0f}x严重")
        elif p.volume_ratio_5m >= vrH:
            # 30-60x: 如果15m量比也异常则更重罚
            base_pen = 4
            if p.volume_ratio_15m > vrW: base_pen = 5  # 多周期异常
            mp += base_pen; sb.penalty_reasons.append(f"量比5m={p.volume_ratio_5m:.0f}x/15m={p.volume_ratio_15m:.1f}x偏高")
        elif p.volume_ratio_5m >= vrW:
            mp+=2; sb.penalty_reasons.append(f"量比{p.volume_ratio_5m:.0f}x警戒")

        if p.change_1m > 5 and p.trades_5m > 0 and p.trades_5m < 15:
            mp+=3; sb.penalty_reasons.append("大涨但笔数极少")
        # V2.8: 纯5m脉冲惩罚加强
        if p.change_5m > 5 and p.change_15m < 1.5 and p.change_1h < 3:
            mp+=3; sb.penalty_reasons.append("纯5m脉冲")

        # V3.0: 量比不一致惩罚 — 5m量比高但15m不跟=单根异常
        if s.vol_ratio_inconsistency_penalty:
            if p.volume_ratio_5m > 5.0 and p.volume_ratio_15m < 1.5:
                mp += 3; sb.penalty_reasons.append(f"量比不一致(5m={p.volume_ratio_5m:.0f}x 15m={p.volume_ratio_15m:.1f}x)")
            # 5m/15m都高但1h不承接
            elif p.volume_ratio_5m > 3.0 and p.volume_ratio_15m > 2.0:
                if p.change_1h < 2 and p.change_4h < 1:
                    mp += 2; sb.penalty_reasons.append("量比短时好看但1h/4h不承接")

        # V3.0: 纯15m脉冲
        if getattr(s, 'pure_15m_pulse_reject', True) and p.change_15m > 6 and p.change_1h < 3 and p.change_4h < 2:
            mp += 3; sb.penalty_reasons.append(f"纯15m脉冲(15m={p.change_15m:.1f}% 1h={p.change_1h:.1f}%)")

        sb.manipulation_risk_penalty = min(mp, 10)

        # 链上风险 0-10
        cr = 0.0
        if md.chain_data:
            cd = md.chain_data
            if cd.pool_age_hours < 48: cr+=2; sb.penalty_reasons.append("池龄<48h")
            if cd.liquidity_usd < 1e5: cr+=2
            if cd.honeypot_flag: cr+=5; sb.penalty_reasons.append("蜜罐")
            if "buy_heavy_suspicious" in cd.contract_risk_tags: cr+=2
            if "vol_mcap_anomaly" in cd.contract_risk_tags: cr+=2; sb.penalty_reasons.append("量/市值异常")
        sb.chain_risk_penalty = min(cr, 10)

        # 执行分 0-10
        ex = 0.0
        if phase == SignalPhase.STARTUP: ex+=6
        elif phase == SignalPhase.ACCELERATION: ex+=4
        elif phase == SignalPhase.WATCH: ex+=3
        elif phase == SignalPhase.OVERHEATED: ex+=1
        if chase.chase_risk_level == ChaseRiskLevel.LOW: ex+=4
        elif chase.chase_risk_level == ChaseRiskLevel.MEDIUM: ex+=2
        sb.execution_score = min(ex, 10)

        total = (sb.momentum_score + sb.volume_quality_score + sb.liquidity_score + sb.continuation_score
                 + sb.breakout_quality_score + sb.execution_score
                 - sb.overheat_penalty - sb.manipulation_risk_penalty - sb.chain_risk_penalty)
        sb.total_score = max(0, min(100, total))
        sb.grade = ScoreGrade.A if sb.total_score >= P["grade_a"] else ScoreGrade.B if sb.total_score >= P["grade_b"] else ScoreGrade.C

        if phase == SignalPhase.WATCH: sb.watch_reasons.append("动能或量能未达标")
        if phase == SignalPhase.REJECT:
            if p.change_1h > P["phase_reject_1h"]: sb.reject_reasons.append(f"1h涨{p.change_1h:.0f}%")
            if chase.is_chase_high_danger: sb.reject_reasons.append("追高危险")
        return sb

    def _advice(self, md: MarketData, sig: Signal) -> TradingAdvice:
        a = TradingAdvice()
        p, P, s = md.periods, self.P, self.settings
        phase, chase, sc = sig.phase, sig.chase_risk, sig.score
        a.execution_priority_score = sc.total_score

        is_leverage = any((md.symbol.split("/")[0] if "/" in md.symbol else md.symbol).upper().endswith(x) for x in ("3L","3S","5L","5S"))
        a.is_suitable_for_24h_trade = (
            phase in (SignalPhase.STARTUP, SignalPhase.ACCELERATION)
            and not chase.is_chase_high_danger
            and sc.total_score >= s.push_min_score
            and p.volume_ratio_5m < s.volume_ratio_heavy_threshold
            and p.change_1h > 0 and p.change_4h > 0
            and p.change_1h < s.main_max_1h_change and p.change_24h < s.main_max_24h_change
            and (not is_leverage or not getattr(s, 'leverage_token_full_block', True))
        )

        if phase == SignalPhase.REJECT:
            a.suggested_action="放弃"; a.suggested_position_size=PositionSize.IGNORE
            a.rejection_reason="; ".join(sc.reject_reasons) or "风险过高"
            a.execution_level = ExecutionLevel.REJECT.value
        elif phase == SignalPhase.OVERHEATED:
            a.suggested_action="观察等回调"; a.suggested_position_size=PositionSize.IGNORE; a.rejection_reason="过热"
            a.execution_level = ExecutionLevel.WATCH.value
        elif chase.should_wait_pullback:
            a.suggested_action="观察等回调"; a.suggested_position_size=PositionSize.IGNORE
            # 追高风险中等但结构尚可 → 挂单; 高追高 → 仅观察
            a.execution_level = ExecutionLevel.LIMIT_ONLY.value if chase.chase_risk_level == ChaseRiskLevel.MEDIUM else ExecutionLevel.WATCH.value
        elif phase == SignalPhase.STARTUP and sc.total_score >= max(P["act_start"], s.push_min_score):
            a.suggested_action="可轻仓试错"; a.suggested_position_size=PositionSize.LIGHT
            a.execution_level = ExecutionLevel.MAIN_SIGNAL.value
        elif phase == SignalPhase.ACCELERATION and sc.total_score >= P["act_accel"]:
            a.suggested_action="可轻仓试错"; a.suggested_position_size=PositionSize.LIGHT
            a.execution_level = ExecutionLevel.MAIN_SIGNAL.value
        elif sc.total_score >= P["act_strong"]:
            a.suggested_action="可轻仓试错"; a.suggested_position_size=PositionSize.MEDIUM
            a.execution_level = ExecutionLevel.MAIN_SIGNAL.value
        else:
            a.suggested_action="观察等回调"; a.suggested_position_size=PositionSize.IGNORE
            a.execution_level = ExecutionLevel.WATCH.value

        if p.change_4h < 8 and p.change_24h < 15: a.recommended_holding_window=HoldingWindow.H24
        elif p.change_1h < 12 and p.change_4h < 15: a.recommended_holding_window=HoldingWindow.H4
        elif p.change_5m > 5: a.recommended_holding_window=HoldingWindow.M15
        else: a.recommended_holding_window=HoldingWindow.H1

        a.not_recommended_if_chasing = chase.chase_risk_level != ChaseRiskLevel.LOW or p.volume_ratio_5m >= s.volume_ratio_heavy_threshold
        a.invalidation_hint = f"跌回5m起涨价(约{md.price*0.97:.6g})或量缩" if p.change_5m > 3 else f"跌破-5%(约{md.price*0.95:.6g})"
        a.take_profit_hint = "分批8-15%,可至24h" if phase==SignalPhase.STARTUP else "快走5-10%,不持过4h" if phase==SignalPhase.ACCELERATION else "不建议"
        a.stop_loss_hint = "止损3-5%"
        a.is_second_wave_opportunity = p.change_4h > 8 and p.change_1h < 3 and p.change_5m > 1
        a.is_near_breakout = p.change_5m > 1.5 and 2.5 < p.volume_ratio_5m < s.volume_ratio_reject_threshold
        return a

    def _triggers(self, md: MarketData, sig: Signal) -> list[str]:
        r, p = [], md.periods; vrW = self.settings.volume_ratio_warning_threshold
        if p.change_5m > 1.5: r.append(f"5m涨{p.change_5m:.1f}%")
        if 1.8 < p.volume_ratio_5m < vrW: r.append(f"5m量比{p.volume_ratio_5m:.1f}x")
        if p.change_15m > 2.5: r.append(f"15m涨{p.change_15m:.1f}%")
        if sig.advice.is_near_breakout: r.append("放量突破")
        if sig.advice.is_second_wave_opportunity: r.append("疑似二波")
        for x in sig.score.score_reasons:
            if x not in r: r.append(x)
            if len(r)>=5: break
        return r[:5]

    def _warnings(self, md: MarketData, sig: Signal) -> list[str]:
        w, ch = [], sig.chase_risk
        if ch.instant_risk: w.append("短线急拉")
        if ch.structural_risk: w.append("结构追高")
        if ch.volume_exhaust_risk: w.append("放量衰竭")
        if ch.fake_breakout_risk: w.append("疑似假突破")
        if ch.second_wave_end_risk: w.append("二波末端")
        if md.chain_data and md.chain_data.contract_risk_tags: w.extend(md.chain_data.contract_risk_tags[:2])
        for x in sig.score.penalty_reasons:
            if x not in w: w.append(x)
            if len(w)>=4: break
        return w[:4]
