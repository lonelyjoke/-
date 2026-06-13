# PROJECT_BRIEF

## 0. 当前工作目录与根目录概览

当前工作目录：

```text
C:\Users\zhanc\Documents\GitHub\-
```

项目根目录主要文件与文件夹：

```text
.agents/
.cargo/
.claude/
.git/
.github/
.vscode/
crates/
czsc/
docs/
scripts/
tests/
.gitignore
AGENTS.md
Cargo.lock
Cargo.toml
CHANGELOG.md
CLAUDE.md
LICENSE
pyproject.toml
README.md
rust-toolchain.toml
uv.lock
```

结论：这是一个明确的缠论 / CZSC 相关项目。README、示例、测试和包入口都显示项目围绕“缠中说禅技术分析”展开，核心能力包括分型、笔、中枢、信号、事件、持仓、策略回测和多级别联立分析。当前 1.x 架构下，缠论核心算法已经迁移到 Rust workspace，通过 PyO3 扩展模块 `czsc._native` 暴露给 Python。

## 1. 项目目录结构

### 顶层结构

- `czsc/`：Python package，对外 API 门面、数据连接器、工具函数、策略封装、CLI、可视化等。
- `crates/`：Rust workspace，承载缠论核心算法、信号函数、交易器、PyO3 binding。
- `docs/examples/`：可直接运行的示例脚本，覆盖快速入门、缠论结构、信号、事件、策略回测、Tushare 数据、HTML 报告等。
- `tests/`：pytest 测试，包括单元测试、CLI 测试、兼容性测试、集成测试和 smoke test。
- `scripts/`：项目脚本目录。
- `pyproject.toml`：Python 包和 maturin 构建配置，声明 `czsc._native` 由 `crates/czsc-python` 产出。
- `Cargo.toml` / `Cargo.lock`：Rust workspace 配置和锁文件。
- `README.md` / `CHANGELOG.md` / `LICENSE`：项目说明、变更记录和许可。

### Python package 结构

- `czsc/__init__.py`：顶层公共 API 聚合入口，导出 `CZSC`、`FX`、`BI`、`ZS`、`RawBar`、`BarGenerator`、`Event`、`Position`、`CzscTrader`、`generate_czsc_signals`、`WeightBacktest` 等。
- `czsc/traders/__init__.py`：交易与信号门面，直接从 `czsc._native` re-export `CzscSignals`、`CzscTrader`、`generate_czsc_signals`、`get_signals_config` 等。
- `czsc/strategies.py`：Python 侧策略抽象，提供 `CzscStrategyBase` / `CzscJsonStrategy`，通过 `positions` 派生信号配置、周期、回测与回放。
- `czsc/connectors/`：行情数据源连接器，包括 Tushare、天勤、CCXT、本地投研数据。
- `czsc/utils/`：缓存、IO、日志、交易工具、可视化、指标分析、数据工具等。
- `czsc/cli/`：命令行入口，覆盖 analyze、backtest、bench、data、plot、research、schema、signals 等。
- `czsc/_format_standard_kline.py`：把标准 DataFrame 转成 `RawBar` 列表，是 Python 数据进入 Rust 缠论分析前的重要适配层。
- `czsc/_resample_bars.py`：K 线重采样适配层。

### Rust workspace 结构

- `crates/czsc-core/`：缠论核心数据结构和算法。
- `crates/czsc-signals/`：信号函数实现与注册。
- `crates/czsc-trader/`：多级别信号计算、交易器、策略运行逻辑。
- `crates/czsc-python/`：PyO3 binding 入口，生成 Python 扩展模块 `czsc._native`。
- `crates/czsc-ta/`：Rust TA 算子。
- `crates/czsc-utils/`：Rust 工具能力。
- `crates/czsc-derive/`、`crates/czsc-signal-macros/`：derive / proc-macro 支持。
- `crates/czsc/`：Rust crate 聚合入口。

## 2. 缠论核心模块：分型、笔、中枢、买卖点与信号生成

### 分型、笔、中枢

核心实现位于 Rust：

- `crates/czsc-core/src/analyze/mod.rs`：`CZSC` 分析对象。
- `crates/czsc-core/src/analyze/utils.rs`：`check_fx`、`check_fxs`、`check_bi` 等分型 / 笔识别工具。
- `crates/czsc-core/src/objects/fx.rs`：`FX` 分型对象。
- `crates/czsc-core/src/objects/bi.rs`：`BI` 笔对象。
- `crates/czsc-core/src/objects/zs.rs`：`ZS` 中枢对象。
- `crates/czsc-core/src/objects/bar.rs`：`RawBar` / `NewBar` K 线对象。
- `crates/czsc-core/src/objects/freq.rs`、`mark.rs`、`direction.rs`：周期、顶底分型标记、方向枚举。

Python 暴露入口：

- `czsc.CZSC`
- `czsc.FX`
- `czsc.BI`
- `czsc.ZS`
- `czsc.RawBar`
- `czsc.NewBar`
- `czsc.check_fx`
- `czsc.check_fxs`
- `czsc.check_bi`
- `czsc.remove_include`
- `czsc.format_standard_kline`

相关测试：

- `tests/unit/test_core_parity.py`：锁定 `CZSC`、分型、笔输出与基线一致，防止缠论核心算法漂移。
- `tests/unit/test_resample_bars.py`：覆盖重采样行为。
- `tests/compat/test_public_api.py`：锁定顶层 API 契约。

### 买卖点、信号函数

信号函数核心实现位于：

- `crates/czsc-signals/src/`
- `crates/czsc-signals/src/cxt.rs`：缠论上下文类信号，包含笔状态、三买等典型缠论信号。
- `crates/czsc-signals/src/bar.rs`：K 线基础状态信号。
- `crates/czsc-signals/src/tas.rs`：技术指标辅助信号。
- `crates/czsc-signals/src/vol.rs`、`obv.rs`、`pressure.rs`、`cvolp.rs` 等：成交量、压力支撑、OBV 等信号。
- `crates/czsc-signals/src/registry.rs`：信号注册表。
- `crates/czsc-signals/src/types.rs`：信号元信息与描述结构。

Python 暴露和调度入口：

- `czsc._native.signals.*`
- `czsc.CzscSignals`
- `czsc.generate_czsc_signals`
- `czsc.get_signals_config`
- `czsc.get_signals_freqs`
- `czsc.derive_signals_config`
- `czsc.derive_signals_freqs`

CLI 和测试入口：

- `czsc/cli/signals.py`
- `tests/cli/test_signals.py`

典型信号示例：

- `cxt_bi_status_V230101`：笔表里关系。
- `cxt_third_buy_V230228`：三买辅助信号。
- `bar_zdt_V230331`：涨跌停状态。
- `tas_ma_base_V221101`：均线分类。

### Event、Position、交易器和风控参数

事件与持仓核心对象位于：

- `crates/czsc-core/src/objects/signal.rs`
- `crates/czsc-core/src/objects/event.rs`
- `crates/czsc-core/src/objects/position.rs`
- `crates/czsc-core/src/objects/operate.rs`
- `crates/czsc-trader/src/czsc_signals.rs`
- `crates/czsc-trader/src/trader.rs`
- `crates/czsc-trader/src/strategy.rs`

Python 使用入口：

- `czsc.Signal`
- `czsc.Event`
- `czsc.Position`
- `czsc.Operate`
- `czsc.CzscSignals`
- `czsc.CzscTrader`
- `czsc.CzscStrategyBase`
- `czsc.CzscJsonStrategy`

`Position` 已包含适合持仓风险管理的关键参数：

- `interval`：开仓间隔约束。
- `timeout`：最长持仓 K 线根数。
- `stop_loss`：止损，BP 单位。
- `t0`：是否允许 T+0。
- `opens` / `exits`：开仓和平仓事件集合。

## 3. 可运行 examples

`docs/examples/` 下当前可运行脚本包括：

- `01_quick_start.py`：快速入门，从 mock K 线到 `CZSC` 分析对象，查看分型和笔。
- `02_chan_structures.py`：深入查看 `FX`、`BI`、`ZS`。
- `04_bar_generator.py`：K 线合成与多级别分析。
- `05_signals.py`：信号配置、流式计算、批量生成、反向解析。
- `06_event_position.py`：`Signal -> Event -> Position`，展示事件匹配和持仓定义。
- `07_strategy_backtest.py`：继承 `CzscStrategyBase` 定义策略，执行 backtest / replay。
- `08_weight_backtest.py`：权重序列回测。
- `13_event_weight_backtest.py`：Event 策略回测 + wbt HTML 报告，使用 mock 数据。
- `13_lightweight_charts_html.py`：导出 lightweight-charts 缠论 HTML。
- `14_tushare_event_backtest.py`：Tushare 真实 A 股 30 分钟 K 线 + Event 策略回测。
- `15_lightweight_signals_html.py`：把信号点叠加到 lightweight HTML 图表。
- `17_perf_benchmark.py`：性能基准。
- `18_tushare_daily_event_universe.py`：Tushare 全市场日线事件选股 + WeightBacktest。

推荐运行顺序：

```powershell
uv run --no-sync python docs/examples/01_quick_start.py
uv run --no-sync python docs/examples/02_chan_structures.py
uv run --no-sync python docs/examples/05_signals.py
uv run --no-sync python docs/examples/06_event_position.py
uv run --no-sync python docs/examples/07_strategy_backtest.py
uv run --no-sync python docs/examples/13_event_weight_backtest.py
```

需要真实 Tushare 数据的示例：

```powershell
$env:TUSHARE_TOKEN="your_token"
uv run --no-sync python docs/examples/14_tushare_event_backtest.py
uv run --no-sync python docs/examples/18_tushare_daily_event_universe.py
```

注意：真实数据示例依赖网络、Tushare token 权限和本地环境。不要把 mock 数据结果当成真实行情研究结论。

## 4. 如何接入本地 Tushare 分钟 K 线数据

项目已有两条相关路径：

### 路径 A：直接使用 `czsc.connectors.ts_connector`

适合在线从 Tushare 拉取数据，并由项目 `DataClient` / Tushare 本身处理缓存。

关键模块：

- `czsc/connectors/ts_connector.py`
- `czsc.utils.data.client.DataClient`

关键函数：

- `pro_bar_minutes(ts_code, sdt, edt, freq="60min", asset="E", adj=None)`
- `get_raw_bars(symbol, freq, sdt, edt, fq="后复权", raw_bar=True)`
- `format_kline(kline, freq)`

典型用法：

```python
import os
import tushare as ts
import czsc
from czsc.connectors.ts_connector import get_raw_bars

token = os.environ["TUSHARE_TOKEN"]
ts.set_token(token)
czsc.set_url_token(token=token, url="http://api.tushare.pro")

bars = get_raw_bars(
    symbol="000001.SZ#E",
    freq="30分钟",
    sdt="20200101",
    edt="20240601",
    fq="后复权",
    raw_bar=True,
)

c = czsc.CZSC(bars)
```

`symbol` 约定：

- 股票：`000001.SZ#E`
- 指数：`000300.SH#I`
- 基金 / ETF：`510300.SH#FD`

`freq` 约定：

- 分钟线：`1分钟`、`5分钟`、`15分钟`、`30分钟`、`60分钟`
- 日线及以上：`日线`、`周线`、`月线`

### 路径 B：本地分钟 K 线文件接入

适合已经把 Tushare 分钟数据落成本地 parquet / csv 的场景。核心要求是把本地数据整理成 `format_standard_kline` 需要的标准列：

```text
dt, symbol, open, high, low, close, vol, amount
```

最小接入示例：

```python
import pandas as pd
import czsc

df = pd.read_parquet("data/minute/000001.SZ_1min.parquet")
df = df.rename(columns={
    "trade_time": "dt",
    "ts_code": "symbol",
})
df["dt"] = pd.to_datetime(df["dt"])
df = df[["dt", "symbol", "open", "high", "low", "close", "vol", "amount"]]
df = df.sort_values("dt").drop_duplicates("dt")

bars_1m = czsc.format_standard_kline(df, freq="1分钟")
bars_30m = czsc.resample_bars(df, "30分钟", raw_bars=True, base_freq="1分钟")
c = czsc.CZSC(bars_30m)
```

如果本地数据目录类似投研共享数据，可参考：

- `czsc/connectors/local_data.py`

该模块通过环境变量 `czsc_research_cache` 指定本地 parquet 根目录，再用 `resample_bars` 从 1 分钟数据合成目标周期。

建议为本地 Tushare 分钟数据新增一层轻量读取函数，而不是改核心算法：

```python
def get_local_tushare_minute_bars(path, freq="30分钟", base_freq="1分钟", raw_bars=True):
    df = pd.read_parquet(path)
    df = df.rename(columns={"trade_time": "dt", "ts_code": "symbol"})
    df["dt"] = pd.to_datetime(df["dt"])
    df = df[["dt", "symbol", "open", "high", "low", "close", "vol", "amount"]]
    df = df.sort_values("dt").drop_duplicates("dt")
    if freq == base_freq:
        return czsc.format_standard_kline(df, freq=base_freq)
    return czsc.resample_bars(df, freq, raw_bars=raw_bars, base_freq=base_freq)
```

数据质量建议：

- 严格使用真实 Tushare 数据，不要在研究报告中静默 fallback 到 mock 数据。
- `TUSHARE_TOKEN` 只从环境变量读取，不要写入代码。
- 分钟线要统一时区和交易时间，删除重复 bar。
- 对股票分钟线要明确复权口径：不复权、前复权、后复权。
- 对停牌、零成交、涨跌停、除权日要保留审计字段或处理日志。
- 大股票池建议按 symbol 分文件缓存，便于断点续跑。

## 5. 后续如何搭建“持仓风险管理与次日操作建议系统”

这个项目已经具备搭建该系统的核心底座：行情标准化、缠论结构识别、信号生成、事件组合、持仓状态、回测评估和 HTML 可视化。建议在不修改核心算法的前提下，新增业务层模块或独立应用来组合这些能力。

### 目标输入

- 当前持仓表：证券代码、持仓数量、成本价、当前市值、组合权重、持仓天数、交易限制等。
- 本地 Tushare 分钟 K 线：至少 1 分钟或 5 分钟，可合成 30 分钟、60 分钟、日线。
- 日线行情与复权因子：用于隔夜风险、趋势级别和次日操作建议。
- 可选外部信息：行业分类、指数成分、财务过滤、公告 / 停复牌 / 涨跌停状态。

### 核心计算层

1. 数据接入与校验
   - 读取本地分钟 K 线。
   - 标准化为 `dt, symbol, open, high, low, close, vol, amount`。
   - 使用 `format_standard_kline` / `resample_bars` 生成多周期 `RawBar`。
   - 对缺失、重复、零成交、异常价格做质量检查。

2. 缠论结构分析
   - 对每个持仓标的构造 `CZSC`。
   - 提取最新分型、最后一笔方向、笔力度、中枢区间、是否背驰或三买 / 三卖相关信号。
   - 多周期联立时使用 `BarGenerator`、`CzscSignals` 或 `CzscTrader`。

3. 信号与事件层
   - 使用 `generate_czsc_signals` 批量生成持仓标的信号。
   - 用 `Event` 表达风险事件和操作事件，例如：
     - 跌破最近中枢下沿；
     - 30 分钟笔向下且日线走弱；
     - 涨停禁买、跌停无法卖出；
     - 三买确认后允许加仓；
     - 超过最长持仓周期触发减仓或复核。
   - 用 `Position` 的 `interval`、`timeout`、`stop_loss`、`t0` 表达基础风控。

4. 持仓风险评分
   - 结构风险：最近一笔方向、中枢位置、关键分型破位。
   - 波动风险：近期 ATR / 振幅 / `mark_volatility`。
   - 流动性风险：成交额、停牌、涨跌停。
   - 组合风险：单票权重、行业集中度、相关性、总仓位。
   - 交易风险：T+1 限制、不能卖出数量、涨跌停封单。

5. 次日操作建议
   - 输出每个持仓的建议动作：持有、减仓、清仓、加仓、观察、禁止交易。
   - 给出触发依据：信号、结构、风险项、关键价格位。
   - 给出次日执行计划：
     - 观察价：前高 / 前低 / 中枢上下沿 / 止损价。
     - 条件单逻辑：若跌破 X 则减仓，若放量站回 Y 则持有。
     - 最大建议仓位与目标仓位。

### 推荐系统结构

```text
data/
  minute/
  daily/
  positions/
  cache/

src/
  data_loader.py          # 本地 Tushare 数据读取与标准化
  chan_analyzer.py        # CZSC / 多周期结构提取
  signal_engine.py        # signals_config 与 generate_czsc_signals
  risk_engine.py          # 风险规则与评分
  advice_engine.py        # 次日操作建议生成
  report.py               # HTML / Excel / Markdown 报告
  run_daily_review.py     # 每日收盘后主流程

reports/
  YYYYMMDD_risk_advice.html
  YYYYMMDD_risk_advice.xlsx
```

### 每日运行流程

1. 收盘后更新本地 Tushare 分钟线和日线缓存。
2. 读取当前持仓。
3. 对每个持仓生成多周期 K 线和 `CZSC` 对象。
4. 计算信号、事件匹配和持仓状态。
5. 汇总风险评分和建议动作。
6. 生成报告：总览、个股明细、关键价位、触发信号、组合风险。
7. 次日盘前复核停复牌、涨跌停预案和可卖数量。

### 建议优先实现的 MVP

- 支持本地 parquet / csv 分钟 K 线读取。
- 支持当前持仓 csv / xlsx 输入。
- 对持仓标的计算 30 分钟、60 分钟、日线三个级别。
- 输出最后一笔方向、最近中枢区间、关键分型价、三买 / 笔状态信号。
- 基于简单规则生成建议：
  - 日线弱 + 30 分钟笔向下：减仓 / 观察止损。
  - 价格跌破最近中枢下沿：降低风险等级。
  - 30 分钟三买成立且日线不弱：允许持有或小幅加仓。
  - 涨停：禁止追买；跌停：标记无法卖出风险。
- 生成一份 Markdown 或 HTML 报告。

### 不建议一开始做的事

- 不要修改 `crates/czsc-core` 的分型 / 笔 / 中枢算法。
- 不要在 Python 侧重写 Rust 已有信号逻辑。
- 不要让样例数据静默替代真实 Tushare 数据。
- 不要先做复杂机器学习预测；先把信号、事件、持仓和风控解释链路跑通。

## 6. 本次阅读后的总体判断

该项目非常适合作为“持仓风险管理与次日操作建议系统”的底层分析引擎。它的优势是：

- 缠论核心结构由 Rust 实现，性能和一致性较好。
- Python 顶层 API 比较完整，适合快速搭建研究和业务流程。
- 已有 Tushare 连接器和真实数据示例。
- 已有 Event / Position / Strategy / WeightBacktest 闭环。
- 可视化和报告已有 HTML 路径。

后续建设重点不应是改核心算法，而是新增业务层：本地数据接入、持仓导入、风险规则、建议生成和报告输出。
