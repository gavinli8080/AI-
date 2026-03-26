# Crypto Radar V2.5 🛰️

**24小时短线候选信号雷达系统**

专为"人工决策 + 多AI二筛"工作流设计。V2.5 新增交易标的合法性校验 + 价格源一致性校验。

## 工作流

1. 系统启动时加载各交易所 active spot symbols 白名单
2. 扫描 5 大交易所 + 链上 DEX（只保留 active 交易对）
3. 两阶段预过滤 → 多周期 enrich → 价格一致性校验
4. 执行合法性闸门 → 评分 → 后过滤 → 去重
5. 高质量候选推送到 Telegram（含验证状态 + 二筛摘要块）
6. 用户复制给 AI 做二筛，人工执行短线交易

## V2.5 核心改进: 合法性校验

### 问题
旧版本可能推送"看起来完整但实际不可执行"的信号——交易对已下架、价格不一致、链上价格混入CEX标签等。

### 解决方案

**1. Active Spot Symbols 白名单**

启动时从各交易所官方元数据接口加载当前可交易的现货市场列表：

| 交易所 | 接口 |
|--------|------|
| Binance | /api/v3/exchangeInfo |
| OKX | /api/v5/public/instruments (SPOT) |
| Bitget | /api/v2/spot/public/symbols |
| Gate.io | /api/v4/spot/currency_pairs |
| Bybit | /v5/market/instruments-info (spot) |

不在白名单中的 symbol → 直接过滤，不会进入评分和推送。

**2. 价格一致性校验**

对通过细预过滤的 CEX 信号，用 1m K线 close 验证 ticker 价格偏差：
- 偏差 ≤ 3% (可配置) → price_verified = true
- 偏差 > 3% → 拒绝主推送

**3. 执行合法性闸门 (Execution Validity Gate)**

推送前六项硬检查：
- source_type 必须明确 (CEX_SPOT 或 DEX)
- DEX 不允许被标为 CEX 交易所名
- CEX 必须 symbol_validated = true
- CEX 市场状态必须 = active
- CEX 必须 price_verified = true
- trade_url 不能为空

**4. Telegram 推送新增字段**

每条推送现在显示：
- 类型: CEX_SPOT / DEX
- 市场状态: active / inactive / delisted / unknown
- 标的验证: ✅通过 / ❌未通过
- 价格验证: ✅通过 / ❌未通过

二筛摘要块也包含完整验证信息。

## 快速部署

```bash
pip install -r requirements.txt
cp .env.example .env
nano .env    # 必填: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
mkdir -p data

# 单次扫描
python main.py --once

# 正式运行
python main.py

# 校准模式
SCORING_MODE=calibration python main.py --once
```

## 调试命令

### 校验交易对合法性

```bash
python main.py --validate-symbol binance LTO/USDT
python main.py --validate-symbol okx BTC/USDT
python main.py --validate-symbol gate PEPE/USDT
```

输出包括：
- 是否在 active spot 白名单中
- 官方 market metadata
- ticker 价格 vs K线 close
- 偏差百分比
- 是否允许推送

### 查看日志中的校验统计

```bash
# 启动时各交易所 active symbols 数量
grep "active spot symbols" data/radar.log

# 每轮校验统计
grep "Validity gate" data/radar.log

# 价格校验统计
grep "Price verification" data/radar.log

# 完整漏斗
grep "Scan complete" data/radar.log
```

## 完整流程

```
fetch_tickers (仅 active symbols)
  ↓
coarse_pre_filter (turnover, 24h change)
  ↓
enrich_multi_period (K线: 1m/5m/15m/1h/4h)
  ↓
fine_pre_filter (5m/15m 涨幅触发, 1h 过热)
  ↓
validate_price_consistency (ticker vs kline)
  ↓
risk_assess + score
  ↓
execution_validity_gate (六项硬检查)
  ↓
post_filter (分数/冷却/每日上限)
  ↓
cluster_dedup
  ↓
push (主推送 + watchlist)
```

## 项目结构

```
crypto_radar_v2/
├── main.py                    # 主程序 (含 --validate-symbol)
├── models.py                  # 数据模型 (含 MarketValidation)
├── config/settings.py         # 配置
├── data_sources/
│   ├── base.py                # 基类 (market cache + price validation)
│   ├── binance.py             # Binance (exchangeInfo)
│   ├── okx.py                 # OKX (instruments)
│   ├── bitget.py              # Bitget (public symbols)
│   ├── gate.py                # Gate.io (currency_pairs)
│   ├── bybit.py               # Bybit (instruments-info)
│   └── dexscreener.py         # DexScreener (DEX, no cache)
├── scoring/scorer.py          # 评分引擎 (3模式)
├── filters/signal_filter.py   # 过滤器 (含 validity gate)
├── risk/manager.py            # 风控
├── notifications/telegram.py  # 推送 (含验证字段)
├── tracking/tracker.py        # 持久化追踪
├── storage/db.py              # SQLite
├── utils/http.py              # HTTP 客户端
├── .env.example
├── requirements.txt
└── README.md
```

## 配置项 (V2.5 新增)

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| PRICE_DEVIATION_THRESHOLD | 3.0 | ticker vs kline 最大偏差% |
| MARKET_CACHE_TTL | 1800 | active symbols 缓存刷新秒 |
| ENABLE_PRICE_VERIFICATION | true | 是否开启价格校验 |

## 后台运行

```bash
# screen
screen -S radar
python main.py

# systemd
sudo systemctl start crypto-radar
sudo journalctl -u crypto-radar -f
```

## 测试

```bash
# 语法检查
python -c "from models import *; from config import *; print('OK')"

# 校验特定交易对
python main.py --validate-symbol binance BTC/USDT

# 单次扫描
python main.py --once

# 查看数据库
sqlite3 data/radar.db "SELECT symbol, source, phase, total_score FROM signals ORDER BY timestamp DESC LIMIT 10;"

# 复盘
python main.py --stats
```
