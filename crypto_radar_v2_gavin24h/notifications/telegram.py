"""
Crypto Radar V2 - Telegram (V2.9)
三层推送视觉明确区分: 主推🎯 / 观察👁 / 保底⏳
V2.9: 结构化AI二筛摘要 + 复核价偏差 + 执行级别
"""
from __future__ import annotations
import logging, aiohttp
from models import Signal, SignalPhase, ChaseRiskLevel, MarketStatus, SignalSourceType, ExecutionLevel
from config.settings import Settings

logger = logging.getLogger("radar.telegram")

PE = {SignalPhase.STARTUP:"🟢",SignalPhase.ACCELERATION:"🔵",SignalPhase.OVERHEATED:"🟠",SignalPhase.WATCH:"⚪",SignalPhase.REJECT:"🔴"}
CE = {ChaseRiskLevel.LOW:"🟢",ChaseRiskLevel.MEDIUM:"🟡",ChaseRiskLevel.HIGH:"🔴"}
SE = {MarketStatus.ACTIVE:"✅",MarketStatus.INACTIVE:"⛔",MarketStatus.DELISTED:"🚫",MarketStatus.UNKNOWN:"❓"}

# 执行级别 emoji
ELE = {
    ExecutionLevel.MAIN_SIGNAL.value: "✅",
    ExecutionLevel.LIMIT_ONLY.value: "📌",
    ExecutionLevel.WATCH.value: "👀",
    ExecutionLevel.AI_REVIEW_ONLY.value: "🤖",
    ExecutionLevel.REJECT.value: "❌",
}

def _fn(n):
    if n<=0: return "N/A"
    if n>=1e9: return f"{n/1e9:.2f}B"
    if n>=1e6: return f"{n/1e6:.2f}M"
    if n>=1e3: return f"{n/1e3:.1f}K"
    return f"{n:.0f}"

def _el_cn(val: str) -> str:
    try:
        return ExecutionLevel(val).cn
    except ValueError:
        return val


class TelegramNotifier:
    def __init__(self, settings: Settings):
        self.token = settings.telegram_bot_token
        self.chat = settings.telegram_chat_id
        self.on = bool(self.token and self.chat)

    # === 主推 ===
    async def push_signal(self, sig: Signal):
        if not self.on: logger.info(f"[DRY] {sig.symbol} {sig.score.total_score:.0f}"); return
        await self._send(self._main_card(sig))

    # === 观察池 ===
    async def push_watchlist(self, sigs: list[Signal], is_fallback: bool = False):
        if not self.on or not sigs: return
        await self._send(self._wl_card(sigs, is_fallback))

    # === 统计/文本 ===
    async def push_text(self, t: str):
        if self.on: await self._send(t)
    async def push_stats(self, st: dict):
        L = ["📊 <b>复盘</b>\n",f"完成:{st.get('total_completed',0)} 胜率:{st.get('win_rate',0):.1f}%\n"]
        for g in "ABC":
            d=st.get(f"grade_{g}",{})
            if d.get("total",0)>0: L.append(f"{g}:{d['total']}次 胜率{d['win_rate']:.0f}% 均盈{d['avg_24h_pct']:+.1f}%")
        await self._send("\n".join(L))
    async def push_scan_stats(self, summary, pushed, wl, elapsed):
        if self.on: await self._send(f"📡 [{elapsed:.1f}s] 主推{pushed} 观察{wl}\n{summary}")

    # =================== 主推卡片 ===================
    def _main_card(self, sig: Signal) -> str:
        p=sig.market_data.periods; a=sig.advice; sc=sig.score; ch=sig.chase_risk; v=sig.market_data.validation
        pe=PE.get(sig.phase,"⚪"); ce=CE.get(ch.chase_risk_level,"⚪"); se=SE.get(v.market_status,"❓")
        el_e = ELE.get(a.execution_level, "❓")
        L = []
        L.append(f"🎯 <b>【主推·{sig.phase.cn}】{sig.symbol}</b>")
        L.append(f"{el_e} <b>执行级别: {_el_cn(a.execution_level)}</b>")
        L.append("━━━━━━━━━━━━━━━━━━")
        L.append(f"🏷 {v.signal_source_type.value} | {se}{v.market_status.value}")
        L.append(f"🔍 标的:{'✅' if v.symbol_validated else '❌'} 价格:{'✅' if v.price_verified else '❌'}")
        if v.price_deviation_pct>0: L.append(f"  偏差:{v.price_deviation_pct:.2f}%")
        # V2.9: 复核价信息
        if v.recheck_price > 0:
            L.append(f"🔄 复核价: ${v.recheck_price:.6g} (偏差{v.recheck_deviation_pct:+.2f}%)")
        if v.source_validation_reason: L.append(f"  ⚠️ {v.source_validation_reason}")
        L.append("━━━━━━━━━━━━━━━━━━")
        L.append(f"📍 {sig.source}")
        if v.market_id_raw: L.append(f"📋 {v.market_id_raw}")
        L.append(f"💰 ${sig.price:.6g}")
        L.append(f"📈 1m {p.change_1m:+.1f}% | 5m {p.change_5m:+.1f}% | 15m {p.change_15m:+.1f}%")
        L.append(f"    1h {p.change_1h:+.1f}% | 4h {p.change_4h:+.1f}% | 24h {p.change_24h:+.1f}%")
        L.append(f"📊 量比 5m {p.volume_ratio_5m:.1f}x | 15m {p.volume_ratio_15m:.1f}x | 1h {p.volume_ratio_1h:.1f}x")
        L.append(f"💎 24h ${_fn(p.turnover_24h)} | 1h ${_fn(p.turnover_1h)}")
        if sig.market_data.chain_data:
            cd=sig.market_data.chain_data; L.append(f"🏦 流动性 ${_fn(cd.liquidity_usd)}")
            if cd.market_cap>0: L.append(f"📊 市值 ${_fn(cd.market_cap)}")
        elif sig.market_data.market_cap>0: L.append(f"📊 市值 ${_fn(sig.market_data.market_cap)}")
        L.append(f"🌊 波动率 {sig.market_data.volatility_24h:.1f}%")
        L.append("━━━━━━━━━━━━━━━━━━")
        L.append(f"⭐ {sc.total_score:.0f}/100 [{sc.grade.value}] 🎯 优先级{a.execution_priority_score:.0f}")
        L.append(f"  动能{sc.momentum_score:.0f} 量能{sc.volume_quality_score:.0f} 流动{sc.liquidity_score:.0f} "
                 f"延续{sc.continuation_score:.0f} 突破{sc.breakout_quality_score:.0f} 执行{sc.execution_score:.0f}")
        if sc.overheat_penalty>0 or sc.manipulation_risk_penalty>0 or sc.chain_risk_penalty>0:
            L.append(f"  扣: 热-{sc.overheat_penalty:.0f} 操-{sc.manipulation_risk_penalty:.0f} 链-{sc.chain_risk_penalty:.0f}")
        L.append("━━━━━━━━━━━━━━━━━━")
        ai={"可轻仓试错":"✅","观察等回调":"👀","放弃":"❌"}.get(a.suggested_action,"❓")
        L.append(f"{ai} <b>{a.suggested_action}</b> {ce}追高:{ch.chase_risk_level.cn}")
        L.append(f"📅 24h短线:{'是' if a.is_suitable_for_24h_trade else '否'} ⏱ 窗口:{a.recommended_holding_window.value}")
        if a.is_second_wave_opportunity: L.append("🔄 二波机会")
        if a.is_near_breakout: L.append("🚀 突破位")
        if sig.trigger_reasons: L.append("\n🎯 <b>触发:</b>"); L.extend(f"  • {r}" for r in sig.trigger_reasons[:5])
        if sig.risk_warnings: L.append("\n⚠️ <b>风险:</b>"); L.extend(f"  • {r}" for r in sig.risk_warnings[:4])
        if a.rejection_reason: L.append(f"\n🚫 {a.rejection_reason}")
        if a.invalidation_hint: L.append(f"❎ 失效: {a.invalidation_hint}")
        if a.take_profit_hint: L.append(f"💰 止盈: {a.take_profit_hint}")
        if a.stop_loss_hint: L.append(f"🛑 止损: {a.stop_loss_hint}")
        if sig.market_data.trade_url: L.append(f"\n🔗 <a href='{sig.market_data.trade_url}'>直达交易</a>")
        L.append(f"\n{'='*30}\n<pre>"); L.append(self._summary(sig)); L.append("</pre>")
        return "\n".join(L)

    # =================== 观察池/保底卡片 ===================
    def _wl_card(self, sigs: list[Signal], is_fallback: bool) -> str:
        L = []
        if is_fallback:
            L.append("⏳ <b>【低信号保底·观察池】</b>")
            L.append("本轮无主推,以下仅为保底观察")
            L.append("🚫 <b>不可直接执行·仅供AI二筛·仅防静默</b>\n")
        else:
            L.append(f"👁 <b>【观察池】</b> {len(sigs)}个候选")
            L.append("⚠️ <b>不可直接执行·仅供观察/补筛/AI二筛</b>\n")

        for sig in sigs[:12]:
            p=sig.market_data.periods; v=sig.market_data.validation; ch=sig.chase_risk
            pe=PE.get(sig.phase,"⚪"); ce=CE.get(ch.chase_risk_level,"⚪")
            el_e = ELE.get(sig.advice.execution_level, "❓")
            L.append(f"{pe} <b>{sig.symbol}</b> [{sig.source}] ({v.signal_source_type.value})")
            L.append(f"  {el_e}{_el_cn(sig.advice.execution_level)} | ${sig.price:.6g}")
            L.append(f"  5m {p.change_5m:+.1f}% 15m {p.change_15m:+.1f}% 1h {p.change_1h:+.1f}% 4h {p.change_4h:+.1f}% 24h {p.change_24h:+.1f}%")
            L.append(f"  vr5m {p.volume_ratio_5m:.1f}x | 分{sig.score.total_score:.0f}[{sig.score.grade.value}] | "
                     f"{ce}{ch.chase_risk_level.cn} | {sig.phase.cn} | 24h${_fn(p.turnover_24h)}")
            if sig.trigger_reasons: L.append(f"  触发:{'; '.join(sig.trigger_reasons[:3])}")
            if sig.risk_warnings: L.append(f"  风险:{'; '.join(sig.risk_warnings[:2])}")
            if sig.market_data.trade_url: L.append(f"  🔗 {sig.market_data.trade_url}")
            L.append("")

        # 结构化AI二筛摘要
        L.append("━━━━━━━━━━━━━━━━━━\n<pre>")
        tag = "保底观察" if is_fallback else "观察池"
        L.append(f"【{tag}·AI二筛摘要】")
        L.append("⚠️ execution_level=ai_review_only 不可直接执行")
        for sig in sigs[:12]:
            p=sig.market_data.periods; v=sig.market_data.validation
            L.append(self._summary_line(sig))
        L.append("</pre>")
        return "\n".join(L)

    # =================== 二筛摘要 (结构化 key:value, V2.9) ===================
    def _summary(self, sig: Signal) -> str:
        """主推信号的结构化摘要 — 方便复制给 ChatGPT/Grok/OpenClaw 做二筛"""
        p=sig.market_data.periods; a=sig.advice; sc=sig.score; v=sig.market_data.validation; ch=sig.chase_risk
        lq = f"${_fn(sig.market_data.chain_data.liquidity_usd)}" if sig.market_data.chain_data else f"CEX(${_fn(p.turnover_24h)})"
        L = [
            "【AI二筛数据·请直接复制给AI分析】",
            f"symbol: {sig.symbol}",
            f"exchange: {sig.source}",
            f"source_type: {v.signal_source_type.value}",
            f"market_status: {v.market_status.value}",
            f"symbol_verified: {'Y' if v.symbol_validated else 'N'}",
            f"price_verified: {'Y' if v.price_verified else 'N'}",
            f"raw_id: {v.market_id_raw}",
            f"price_at_scan: ${sig.price:.6g}",
            f"price_deviation_pct: {v.price_deviation_pct:.2f}%",
        ]
        # V2.9: 复核价
        if v.recheck_price > 0:
            L.append(f"recheck_price: ${v.recheck_price:.6g}")
            L.append(f"recheck_deviation_pct: {v.recheck_deviation_pct:.2f}%")
        L.extend([
            f"change_1m: {p.change_1m:+.2f}%",
            f"change_5m: {p.change_5m:+.2f}%",
            f"change_15m: {p.change_15m:+.2f}%",
            f"change_1h: {p.change_1h:+.2f}%",
            f"change_4h: {p.change_4h:+.2f}%",
            f"change_24h: {p.change_24h:+.2f}%",
            f"vol_ratio_5m: {p.volume_ratio_5m:.2f}x",
            f"vol_ratio_15m: {p.volume_ratio_15m:.2f}x",
            f"vol_ratio_1h: {p.volume_ratio_1h:.2f}x",
            f"turnover_24h: ${_fn(p.turnover_24h)}",
            f"turnover_1h: ${_fn(p.turnover_1h)}",
            f"liquidity: {lq}",
            f"market_cap: ${_fn(sig.market_data.market_cap)}",
            f"volatility_24h: {sig.market_data.volatility_24h:.1f}%",
            f"phase: {sig.phase.value}",
            f"score: {sc.total_score:.0f}/100",
            f"grade: {sc.grade.value}",
            f"score_detail: mom={sc.momentum_score:.0f} vol={sc.volume_quality_score:.0f} liq={sc.liquidity_score:.0f} cont={sc.continuation_score:.0f} brk={sc.breakout_quality_score:.0f} exe={sc.execution_score:.0f}",
            f"penalties: heat=-{sc.overheat_penalty:.0f} manip=-{sc.manipulation_risk_penalty:.0f} chain=-{sc.chain_risk_penalty:.0f}",
            f"execution_level: {a.execution_level}",
            f"action: {a.suggested_action}",
            f"chase_risk: {ch.chase_risk_level.value}",
            f"suitable_24h: {'Y' if a.is_suitable_for_24h_trade else 'N'}",
            f"holding_window: {a.recommended_holding_window.value}",
            f"position_size: {a.suggested_position_size.value}",
            f"triggers: {'; '.join(sig.trigger_reasons[:5])}",
            f"risks: {'; '.join(sig.risk_warnings[:4])}",
            f"rejection: {a.rejection_reason or 'none'}",
            f"invalidation: {a.invalidation_hint}",
            f"take_profit: {a.take_profit_hint}",
            f"stop_loss: {a.stop_loss_hint}",
        ])
        if sig.market_data.chain_data:
            cd=sig.market_data.chain_data
            L.append(f"chain: {cd.chain}")
            L.append(f"pool_age_hours: {cd.pool_age_hours:.0f}")
            L.append(f"buy_sell_ratio: {cd.buys_vs_sells_ratio:.2f}")
        if sig.market_data.trade_url: L.append(f"trade_url: {sig.market_data.trade_url}")
        return "\n".join(L)

    def _summary_line(self, sig: Signal) -> str:
        """观察池单行结构化摘要"""
        p=sig.market_data.periods; v=sig.market_data.validation; a=sig.advice
        return (
            f"{sig.symbol}|{sig.source}|{v.signal_source_type.value}|"
            f"${sig.price:.6g}|"
            f"5m:{p.change_5m:+.1f}% 1h:{p.change_1h:+.1f}% 4h:{p.change_4h:+.1f}% 24h:{p.change_24h:+.1f}%|"
            f"vr5m:{p.volume_ratio_5m:.1f}x|"
            f"score:{sig.score.total_score:.0f}{sig.score.grade.value}|"
            f"phase:{sig.phase.value}|"
            f"exec:{a.execution_level}|"
            f"chase:{sig.chase_risk.chase_risk_level.value}|"
            f"24h_ok:{'Y' if a.is_suitable_for_24h_trade else 'N'}"
        )

    # =================== 发送 ===================
    async def _send(self, text: str):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        pl = {"chat_id":self.chat,"text":text,"parse_mode":"HTML","disable_web_page_preview":True}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=pl, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        b = await r.text()
                        if "too long" in b.lower() or r.status==400: await self._chunked(text)
                        else: logger.error(f"TG {r.status}: {b[:200]}")
        except Exception as e: logger.error(f"TG: {e}")

    async def _chunked(self, text: str, sz: int = 4000):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        chunks = [text[i:i+sz] for i in range(0, len(text), sz)]
        async with aiohttp.ClientSession() as s:
            for i, ch in enumerate(chunks):
                pl = {"chat_id":self.chat,"text":ch,"disable_web_page_preview":True}
                if i==0: pl["parse_mode"]="HTML"
                try:
                    async with s.post(url, json=pl, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        if r.status!=200: logger.error(f"TG chunk{i}: {r.status}")
                except Exception as e: logger.error(f"TG chunk{i}: {e}")
