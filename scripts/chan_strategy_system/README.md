# Chan Strategy System

This folder contains a testable Chan strategy system built on the existing CZSC examples.

It now has two layers:

- **V1 三买回测**：单周期三买事件驱动回测，用来验证交易链路和基准策略表现。
- **V2 Core 主版本底座**：多级别结构识别、候选买卖点提取、信号统计检验入口，用来支撑后续策略假设检验。

## V1 What It Does

The MVP uses the strategy pattern already shown in `docs/examples/13_event_weight_backtest.py` and `docs/examples/14_tushare_event_backtest.py`:

- Open long: `{freq}_D1_三买辅助V230228_三买_任意_任意_0`
- Exit long: `{freq}_D1_表里关系V230101_向下_任意_任意_0`
- No-open guard: `{freq}_D1_涨跌停V230331_涨停_任意_任意_0`
- Backtest engine: `CzscStrategyBase.backtest`
- Portfolio evaluation: `WeightBacktest`

It does not modify the CZSC core algorithm.

## V2 Core What It Does

V2 Core is not a final trading strategy. It is the research base for a more complete Chan strategy:

- Multi-level analysis: default `日线,60分钟,30分钟,15分钟`
- Structure state: trend, BI status, MA state, FX / BI counts, recent risk price
- Candidate signals: 一买、二买、三买、一卖、二卖
- Signal statistics: future 1 / 3 / 5 / 10 / 20 bar return mean and win rate
- Decision summary: score, rating, action suggestion, and reasons

V2 outputs a timestamped folder containing:

- `v2_config.json`
- `v2_summary.json`
- `v2_latest_states.csv`
- `v2_candidates.csv`
- `v2_signal_stats.csv`
- `v2_report.md`

## Inputs

Minimum custom-pool input:

```csv
symbol
000001.SZ
600519.SH
300750.SZ
```

Real-data universe input can also be pulled from Tushare index weights:

- `pool=csi500-history`
- `index_code=000905.SH`

The first version uses Tushare `index_weight` to build a point-in-time membership filter where possible. For very old or sparse index weight records, rows before the first available index weight date are excluded.

## Outputs

Each run writes a timestamped folder under `data/reports/chan_strategy/`:

- `config.json`
- `symbols.csv`
- `per_symbol_summary.csv`
- `portfolio_weights.csv`
- `portfolio_stats.csv` when trades exist
- `portfolio_report.html` when HTML generation succeeds
- `report.md`

Local bars and index weights are cached under `data/cache/chan_strategy/`.

## Safe Token Handling

Do not paste the Tushare token into source files or chat. Set it locally:

```powershell
$env:TUSHARE_TOKEN="your_token"
$env:TUSHARE_HTTP_URL="https://tt.dailyfetch.top/"
```

The scripts only print whether the token exists and its length. The custom Tushare endpoint is initialized in `tushare_client.py`; do not hard-code tokens in source files.

## Dry Environment Check

```powershell
uv run --no-sync python scripts/chan_strategy_system/check_env.py
```

Optional real network check:

```powershell
uv run --no-sync python scripts/chan_strategy_system/check_env.py --network-check
```

## Local Input UI

Start the local browser UI:

```powershell
uv run --no-sync python scripts/chan_strategy_system/ui.py
```

Then open:

```text
http://127.0.0.1:8765
```

The default symbol is `002202.SZ` (金风科技). You only need to change the stock code and click the run button.

The UI has an **分析版本** selector:

- `V2 主版本底座`: default; generates structure and signal research reports.
- `V1 三买回测`: keeps the original event backtest and equity curve.

## Mock Pipeline Test

Use mock data to verify the local pipeline without Tushare:

```powershell
uv run --no-sync python scripts/chan_strategy_system/backtest.py --mode mock --pool custom --symbols 000001.SZ,000002.SZ --start-date 20200101 --end-date 20210101 --backtest-start 20200701 --limit 2
```

Mock runs are for code validation only. They are not real-data research.

V2 Core mock run:

```powershell
uv run --no-sync python scripts/chan_strategy_system/chan_core.py --mode mock --symbols 002202.SZ --start-date 20200101 --end-date 20210101 --backtest-start 20200701 --analysis-freqs 60分钟,30分钟
```

## Csi500 Real-Data Backtest

Small smoke run:

```powershell
$env:TUSHARE_TOKEN="your_token"
uv run --no-sync python scripts/chan_strategy_system/backtest.py --mode tushare --pool csi500-history --index-code 000905.SH --start-date 20200101 --end-date 20240601 --backtest-start 20200701 --base-freq 30分钟 --limit 10
```

Larger run:

```powershell
$env:TUSHARE_TOKEN="your_token"
uv run --no-sync python scripts/chan_strategy_system/backtest.py --config scripts/chan_strategy_system/config.example.json
```

Real-data mode is strict. If Tushare fails, the run stops instead of silently using sample data.
