# Crypto Radar V2 - Gavin 24h 定制版

这是基于你上传的 `crypto radar v2 9` 做的保守型 24h 短线定制版。

## 主要改动
- 默认更严格：`SCORING_MODE=strict`
- 主推要求更保守：
  - 1h > 0
  - 4h > 0
  - 24h 不可过热（默认 < 18%）
  - 1h 不可过热（默认 < 8.5%）
  - 24h 成交额更高（默认 >= 1M）
  - 1h 成交额更高（默认 >= 120K）
- 杠杆代币（3L/3S/5L/5S）默认只进观察池，不进主推
- 异常量比阈值更严格：12/20/35/50
- 适合“纸飞机初筛 -> 多AI二筛 -> 你自己实盘”的流程

## 快速部署
```bash
chmod +x deploy.sh
./deploy.sh
cp .env.example .env
nano .env
./run_once.sh
./run_live.sh
```

## 多AI二筛
`prompts/` 目录放了 OpenClaw 和 Grok 的提示词。
