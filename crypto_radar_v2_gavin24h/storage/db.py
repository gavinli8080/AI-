"""
Crypto Radar V2 - SQLite 存储
信号表、追踪表、持久化、CSV导出
"""
from __future__ import annotations
import os
import csv
import json
import time
import sqlite3
import logging
from typing import Optional
from models import Signal, TrackingRecord, ScoreGrade

logger = logging.getLogger("radar.storage")


class Database:
    def __init__(self, db_path: str = "data/radar.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        cur = self.conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            price REAL,
            timestamp REAL,
            phase TEXT,
            grade TEXT,
            total_score REAL,
            execution_priority REAL,
            chase_risk_level TEXT,
            suggested_action TEXT,
            is_suitable_24h INTEGER,
            holding_window TEXT,
            position_size TEXT,
            trigger_reasons TEXT,
            risk_warnings TEXT,
            rejection_reason TEXT,
            score_breakdown TEXT,
            market_data_json TEXT,
            pushed INTEGER DEFAULT 0,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS tracking (
            signal_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            entry_price REAL,
            entry_time REAL,
            phase_at_entry TEXT,
            grade_at_entry TEXT,
            score_at_entry REAL,
            price_15m REAL DEFAULT 0,
            price_1h REAL DEFAULT 0,
            price_4h REAL DEFAULT 0,
            price_24h REAL DEFAULT 0,
            high_price REAL DEFAULT 0,
            low_price REAL DEFAULT 0,
            max_gain_pct REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 0,
            result_24h_pct REAL DEFAULT 0,
            is_completed INTEGER DEFAULT 0,
            completed_time REAL DEFAULT 0,
            next_check_time REAL DEFAULT 0,
            check_stage TEXT DEFAULT '15m'
        );

        CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
        CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
        CREATE INDEX IF NOT EXISTS idx_tracking_completed ON tracking(is_completed);
        CREATE INDEX IF NOT EXISTS idx_tracking_next_check ON tracking(next_check_time);
        """)
        self.conn.commit()

    # =================== 信号存储 ===================

    def save_signal(self, sig: Signal):
        """保存信号到数据库"""
        score_bd = {
            "momentum": sig.score.momentum_score,
            "volume_quality": sig.score.volume_quality_score,
            "liquidity": sig.score.liquidity_score,
            "continuation": sig.score.continuation_score,
            "breakout_quality": sig.score.breakout_quality_score,
            "overheat_penalty": sig.score.overheat_penalty,
            "manipulation_penalty": sig.score.manipulation_risk_penalty,
            "chain_risk_penalty": sig.score.chain_risk_penalty,
            "execution": sig.score.execution_score,
            "score_reasons": sig.score.score_reasons,
            "penalty_reasons": sig.score.penalty_reasons,
        }

        md_json = {
            "change_1m": sig.market_data.periods.change_1m,
            "change_5m": sig.market_data.periods.change_5m,
            "change_15m": sig.market_data.periods.change_15m,
            "change_1h": sig.market_data.periods.change_1h,
            "change_4h": sig.market_data.periods.change_4h,
            "change_24h": sig.market_data.periods.change_24h,
            "vol_ratio_5m": sig.market_data.periods.volume_ratio_5m,
            "vol_ratio_15m": sig.market_data.periods.volume_ratio_15m,
            "vol_ratio_1h": sig.market_data.periods.volume_ratio_1h,
            "turnover_1h": sig.market_data.periods.turnover_1h,
            "turnover_24h": sig.market_data.periods.turnover_24h,
            "volatility": sig.market_data.volatility_24h,
            "market_cap": sig.market_data.market_cap,
            "trade_url": sig.market_data.trade_url,
        }

        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO signals
                (id, symbol, source, price, timestamp, phase, grade, total_score,
                 execution_priority, chase_risk_level, suggested_action,
                 is_suitable_24h, holding_window, position_size,
                 trigger_reasons, risk_warnings, rejection_reason,
                 score_breakdown, market_data_json, pushed, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                sig.id, sig.symbol, sig.source, sig.price, sig.timestamp,
                sig.phase.value, sig.score.grade.value, sig.score.total_score,
                sig.advice.execution_priority_score, sig.chase_risk.chase_risk_level.value,
                sig.advice.suggested_action,
                1 if sig.advice.is_suitable_for_24h_trade else 0,
                sig.advice.recommended_holding_window.value,
                sig.advice.suggested_position_size.value,
                json.dumps(sig.trigger_reasons, ensure_ascii=False),
                json.dumps(sig.risk_warnings, ensure_ascii=False),
                sig.advice.rejection_reason,
                json.dumps(score_bd, ensure_ascii=False),
                json.dumps(md_json, ensure_ascii=False),
                1 if sig.pushed else 0,
                time.time(),
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"save_signal error: {e}")

    def get_recent_signals(self, hours: int = 24, limit: int = 100) -> list[dict]:
        """获取最近N小时的信号"""
        since = time.time() - hours * 3600
        cur = self.conn.execute(
            "SELECT * FROM signals WHERE timestamp > ? ORDER BY timestamp DESC LIMIT ?",
            (since, limit)
        )
        return [dict(row) for row in cur.fetchall()]

    # =================== 追踪存储 ===================

    def save_tracking(self, tr: TrackingRecord):
        """保存或更新追踪记录"""
        try:
            # 计算下次检查时间
            now = time.time()
            if tr.is_completed:
                next_check = 0
                check_stage = "done"
            elif tr.price_15m == 0:
                next_check = tr.entry_time + 900   # 15m后
                check_stage = "15m"
            elif tr.price_1h == 0:
                next_check = tr.entry_time + 3600  # 1h后
                check_stage = "1h"
            elif tr.price_4h == 0:
                next_check = tr.entry_time + 14400 # 4h后
                check_stage = "4h"
            elif tr.price_24h == 0:
                next_check = tr.entry_time + 86400 # 24h后
                check_stage = "24h"
            else:
                next_check = 0
                check_stage = "done"

            self.conn.execute("""
                INSERT OR REPLACE INTO tracking
                (signal_id, symbol, source, entry_price, entry_time,
                 phase_at_entry, grade_at_entry, score_at_entry,
                 price_15m, price_1h, price_4h, price_24h,
                 high_price, low_price, max_gain_pct, max_drawdown_pct,
                 result_24h_pct, is_completed, completed_time,
                 next_check_time, check_stage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                tr.signal_id, tr.symbol, tr.source, tr.entry_price, tr.entry_time,
                tr.phase_at_entry, tr.grade_at_entry, tr.score_at_entry,
                tr.price_15m, tr.price_1h, tr.price_4h, tr.price_24h,
                tr.high_price, tr.low_price, tr.max_gain_pct, tr.max_drawdown_pct,
                tr.result_24h_pct, 1 if tr.is_completed else 0, tr.completed_time,
                next_check, check_stage,
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"save_tracking error: {e}")

    def get_pending_tracking(self) -> list[dict]:
        """获取所有需要检查的追踪任务(next_check_time <= now 且 未完成)"""
        now = time.time()
        cur = self.conn.execute(
            """SELECT * FROM tracking
               WHERE is_completed = 0 AND next_check_time > 0 AND next_check_time <= ?
               ORDER BY next_check_time ASC""",
            (now,)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_all_tracking(self, completed: Optional[bool] = None) -> list[dict]:
        """获取追踪记录"""
        if completed is None:
            cur = self.conn.execute("SELECT * FROM tracking ORDER BY entry_time DESC")
        else:
            cur = self.conn.execute(
                "SELECT * FROM tracking WHERE is_completed = ? ORDER BY entry_time DESC",
                (1 if completed else 0,)
            )
        return [dict(row) for row in cur.fetchall()]

    # =================== CSV 导出 ===================

    def export_signals_csv(self, filepath: str = "data/signals_export.csv"):
        """导出信号表为CSV"""
        cur = self.conn.execute("SELECT * FROM signals ORDER BY timestamp DESC")
        rows = cur.fetchall()
        if not rows:
            logger.info("No signals to export")
            return

        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(rows[0].keys())
            for row in rows:
                writer.writerow(list(row))
        logger.info(f"Exported {len(rows)} signals to {filepath}")

    def export_tracking_csv(self, filepath: str = "data/tracking_export.csv"):
        """导出追踪表为CSV"""
        cur = self.conn.execute("SELECT * FROM tracking ORDER BY entry_time DESC")
        rows = cur.fetchall()
        if not rows:
            logger.info("No tracking to export")
            return

        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(rows[0].keys())
            for row in rows:
                writer.writerow(list(row))
        logger.info(f"Exported {len(rows)} tracking records to {filepath}")

    # =================== 复盘统计 ===================

    def get_performance_stats(self) -> dict:
        """获取复盘统计数据"""
        stats = {}

        # 总体胜率
        cur = self.conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN result_24h_pct > 0 THEN 1 ELSE 0 END) as wins "
            "FROM tracking WHERE is_completed = 1"
        )
        row = cur.fetchone()
        total = row["total"] or 0
        wins = row["wins"] or 0
        stats["total_completed"] = total
        stats["win_rate"] = (wins / total * 100) if total > 0 else 0
        stats["wins"] = wins

        # 按评级统计
        for grade in ["A", "B", "C"]:
            cur = self.conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN result_24h_pct > 0 THEN 1 ELSE 0 END) as wins, "
                "AVG(result_24h_pct) as avg_pct, "
                "AVG(max_gain_pct) as avg_max_gain, "
                "AVG(max_drawdown_pct) as avg_max_dd "
                "FROM tracking WHERE is_completed = 1 AND grade_at_entry = ?",
                (grade,)
            )
            row = cur.fetchone()
            t = row["total"] or 0
            w = row["wins"] or 0
            stats[f"grade_{grade}"] = {
                "total": t,
                "win_rate": (w / t * 100) if t > 0 else 0,
                "avg_24h_pct": round(row["avg_pct"] or 0, 2),
                "avg_max_gain": round(row["avg_max_gain"] or 0, 2),
                "avg_max_drawdown": round(row["avg_max_dd"] or 0, 2),
            }

        # 按阶段统计
        for phase in ["startup", "acceleration", "overheated", "watch"]:
            cur = self.conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN result_24h_pct > 0 THEN 1 ELSE 0 END) as wins, "
                "AVG(result_24h_pct) as avg_pct "
                "FROM tracking WHERE is_completed = 1 AND phase_at_entry = ?",
                (phase,)
            )
            row = cur.fetchone()
            t = row["total"] or 0
            w = row["wins"] or 0
            stats[f"phase_{phase}"] = {
                "total": t,
                "win_rate": (w / t * 100) if t > 0 else 0,
                "avg_24h_pct": round(row["avg_pct"] or 0, 2),
            }

        # 按交易所统计
        cur2 = self.conn.execute(
            "SELECT source, COUNT(*) as total, "
            "SUM(CASE WHEN result_24h_pct > 0 THEN 1 ELSE 0 END) as wins, "
            "AVG(result_24h_pct) as avg_pct "
            "FROM tracking WHERE is_completed = 1 GROUP BY source"
        )
        stats["by_source"] = {}
        for row in cur2.fetchall():
            t = row["total"] or 0
            w = row["wins"] or 0
            stats["by_source"][row["source"]] = {
                "total": t,
                "win_rate": (w / t * 100) if t > 0 else 0,
                "avg_24h_pct": round(row["avg_pct"] or 0, 2),
            }

        return stats

    def close(self):
        self.conn.close()
