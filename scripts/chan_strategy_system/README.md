# 缠论策略分析系统说明

这个目录是我们基于 CZSC 项目搭建的缠论量化策略系统。它的目标不是一次性做出“预测明天涨跌”的黑箱模型，而是逐步建立一套可以检验、可以复盘、可以迭代的交易辅助系统。

系统目前有两个版本：

- **V1 三买回测**：一个最小可用的事件驱动交易策略，用来验证数据、信号、回测、报告这条链路是否跑通。
- **V2 Core 主版本底座**：一个更完整的缠论结构分析和信号研究底座，用来提取多级别结构、候选买卖点和信号统计特征，为后续假设检验和策略优化做准备。

两个版本的定位不同。V1 是“交易策略原型”，回答“单独做三买有没有效果”；V2 是“研究底座”，回答“这只股票当前在多级别缠论结构里处于什么状态，哪些信号值得统计检验”。

## 缠论基础

缠论是一套以价格结构为核心的技术分析方法。它并不是简单看均线、MACD 或某个指标金叉，而是试图把走势拆成多个层次：

- **分型**：局部高点和低点，可以理解为价格短期转折的基本标记。
- **笔**：由一组分型连接出来的上涨或下跌段，是缠论里描述走势方向的基本结构。
- **中枢**：多段走势重叠形成的价格区域，可以粗略理解为市场反复争夺的平衡区。
- **买卖点**：价格离开、回踩、背驰或破坏中枢结构时产生的操作候选点。
- **多级别联立**：同一只股票可以同时看日线、60分钟、30分钟、15分钟等周期。大级别判断环境，小级别寻找具体进出场。

对实盘来说，一个买点不应该孤立存在。更合理的判断流程是：

```text
大级别环境是否值得做？
当前价格在中枢上方、内部还是下方？
操作级别有没有一买、二买、三买等候选信号？
小级别是否确认止跌或转强？
如果判断错了，风险位在哪里？
历史上类似信号表现如何？
```

这个系统就是围绕这几个问题逐步搭建的。

## 为什么要分 V1 和 V2

量化策略迭代最怕“一口气加很多规则”。如果一次性加入三买、二买、多级别、指数过滤、止盈止损和仓位管理，即使结果变好，也不知道到底是哪条规则有效；如果结果变差，也不知道问题出在哪里。

所以我们先把系统拆成两个层次：

```text
V1：验证一条明确交易规则能否跑通
V2：构建更完整的缠论结构和信号研究底座
```

后续优化不是简单从 V1 变成 V2、V3、V4 越来越复杂，而是在 V2 底座上做可比较的实验：

```text
只做三买
三买 + 大级别趋势过滤
三买 + 小级别确认
二买 + 三买组合
买点系统 + 卖点退出
买点系统 + 指数环境过滤
```

每次只验证一个主要假设，比较收益、胜率、回撤、交易次数、盈亏比和样本外表现。

## 系统输入

最简单的输入就是一个股票或 ETF 代码：

```text
002202.SZ
510300.SH
```

网页里默认只需要填写代码，然后点击开始分析。

高级参数包括：

- **数据开始 / 数据结束**：用于拉取历史 K 线。
- **回测开始**：信号统计或回测真正开始的时间，前面的数据作为预热。
- **复权方式**：前复权或后复权。
- **V2 多级别分析周期**：默认 `日线,60分钟,30分钟`。
- **V1 回测周期**：只在选择 V1 时使用，默认 `30分钟`。

## V1 三买回测

### Motivation

V1 的目的不是做完整缠论策略，而是先建立最小可用交易闭环：

```text
Tushare 数据 -> CZSC K线格式 -> 缠论信号 -> 开平仓事件 -> 回测报告 -> 网页展示
```

它回答的问题是：

```text
单独使用 30分钟或60分钟三买信号，是否有可观察的交易价值？
```

如果 V1 都不能稳定跑通，后面做更复杂的多级别策略就没有基础。

### 策略思路

V1 是一个单周期多头策略：

- 出现三买信号时尝试开多；
- 如果当前 K 线涨停，则不开仓，避免无法成交或追高；
- 当笔状态转向下时平多；
- 同时带有基础止损、超时退出和交易间隔控制。

核心信号：

```text
开仓：{freq}_D1_三买辅助V230228_三买_任意_任意_0
平仓：{freq}_D1_表里关系V230101_向下_任意_任意_0
过滤：{freq}_D1_涨跌停V230331_涨停_任意_任意_0
```

其中 `{freq}` 可以是 `30分钟` 或 `60分钟`。

### 执行流程

V1 对每只股票执行：

```text
1. 拉取分钟 K 线
2. 转换为 CZSC RawBar
3. 构建三买开仓事件和平仓事件
4. 使用 CzscStrategyBase.backtest 生成持仓序列
5. 使用 WeightBacktest 计算组合表现
6. 输出回测曲线、交易明细和指标
```

### 主要输出

V1 每次运行会生成：

- `config.json`：本次运行参数。
- `symbols.csv`：本次分析标的。
- `per_symbol_summary.csv`：单票摘要。
- `portfolio_weights.csv`：持仓权重序列。
- `pairs.csv`：交易明细。
- `portfolio_stats.csv`：组合指标。
- `portfolio_report.html`：HTML 回测报告。
- `report.md`：Markdown 摘要报告。

网页会重点展示：

- 回测曲线；
- 交易次数；
- 胜率；
- 单笔收益；
- 最大回撤；
- 最近交易明细；
- 数据质量提示。

### 如何解读 V1

V1 胜率低并不一定说明策略完全无效。你需要同时看：

- 交易次数是否足够；
- 平均盈利是否大于平均亏损；
- 最大回撤是否可接受；
- 信号是否只在某些年份或某些行情有效；
- 是入场信号差，还是退出规则差。

V1 的真正价值是建立一个可比较基准。后续任何优化，都应该和 V1 比较。

## V2 Core 主版本底座

### Motivation

只做三买并不等于完整缠论策略。缠论更强调：

- 一买、二买、三买等不同类型买点；
- 一卖、二卖、三卖等风险提示；
- 中枢位置和趋势结构；
- 多级别联立；
- 信号出现后的统计表现。

V2 Core 的目标是把这些内容整理成一个研究底座。它不是最终交易策略，而是让我们能回答：

```text
当前这只股票在日线、60分钟、30分钟、15分钟分别是什么状态？
最近有没有一买、二买、三买、一卖、二卖等候选信号？
这些信号历史上出现后，未来几根 K 线平均涨跌如何？
当前结构是偏强、震荡还是偏弱？
有没有明确风险位？
```

### 思路

V2 Core 把系统拆成五层：

```text
1. 数据层：从 Tushare 拉取日线和分钟线，并缓存到本地
2. 结构层：识别多级别趋势、笔状态、分型/笔数量和风险位
3. 信号层：提取一买、二买、三买、一卖、二卖等候选信号
4. 统计层：计算信号出现后未来 1/3/5/10/20 根 K 线收益
5. 决策层：生成综合评分、评级、操作建议和主要依据
```

这样做的好处是：我们可以先看清楚结构和信号，再决定哪些信号组合值得变成交易策略。

### 当前支持的分析内容

V2 默认分析周期：

```text
日线,60分钟,30分钟
```

每个周期会尝试生成：

- 笔状态；
- 最近趋势状态；
- 5周期 / 20周期均线状态；
- 分型数量；
- 笔数量；
- 最近候选买卖点；
- 最近风险位。

当前候选信号包括：

- 一买；
- 二买；
- 三买；
- 一卖；
- 二卖；
- 部分版本环境可用时，还会兼容更多缠论信号。

不同 CZSC 安装版本支持的信号函数可能不完全一致。系统会尽量兼容旧版 Python 信号函数和新版 Rust 信号注册表；某个信号不可用时，会跳过该信号并继续分析。

### 执行流程

V2 对每只股票执行：

```text
1. 自动识别股票 / ETF 类型
2. 拉取日线、60分钟、30分钟等 K 线
3. 转换为 CZSC RawBar
4. 批量生成缠论信号
5. 提取最近候选买卖点
6. 计算信号出现后的未来收益统计
7. 汇总多级别结构状态
8. 生成综合评分和操作建议
```

对 ETF，例如 `510300.SH`，系统会自动识别为 Tushare `FD` 类型；普通股票如 `002202.SZ` 会识别为 `E` 类型。

### 运行模式

V2 现在分为两个运行口径：

- `快速巡检 quick`：网页默认模式。它会使用本地K线缓存，并截取各级别最近一段K线生成结构和信号诊断，适合每天输入持仓代码快速查看“当前怎么看”。这个模式速度更快，但不适合直接作为完整统计检验结论。
- `完整研究 standard`：命令行默认模式。它使用参数范围内的全量K线，适合后续做信号统计、假设检验和样本外验证。第一次运行会更慢。

系统还会缓存 V2 信号结果。也就是说，同一只股票、同一日期范围、同一复权方式和同一运行模式，第一次运行需要生成信号；第二次通常会直接读取信号缓存，速度会明显提升。

### 主要输出

V2 每次运行会生成：

- `v2_config.json`：本次 V2 参数。
- `v2_summary.json`：综合评分、评级、建议和主要依据。
- `v2_latest_states.csv`：多级别最新结构状态。
- `v2_candidates.csv`：候选买卖点明细。
- `v2_signal_stats.csv`：信号未来收益统计。
- `v2_report.md`：Markdown 报告。

网页会重点展示：

- 综合评分；
- 状态评级；
- 操作建议；
- 多级别结构状态；
- 最近候选买卖点；
- 信号统计表；
- 原始报告和运行日志。

### 如何解读 V2

V2 不是让你看到一个高分就立刻买入。它更像分析师工作台：

```text
高分：结构和近期买点有一定共振，可以进入重点观察
中分：有机会，但需要等待小级别确认
低分：结构偏弱或统计表现不足，暂不主动参与
```

信号统计表里更重要的是：

- `count`：信号出现次数，太少说明样本不可靠；
- `ret_5b_mean`：信号后未来 5 根 K 线平均收益；
- `ret_5b_win_rate`：信号后未来 5 根 K 线上涨比例；
- `ret_10b_mean` / `ret_20b_mean`：更长观察窗口的收益表现。

网页里的“历史统计”默认会统计一买、二买、三买、一卖、二卖等候选信号。若某个信号没有出现在白话摘要里，通常不是没统计，而是因为：

- 该信号本次没有出现；
- 样本数少于 10 次，暂时不适合作为统计结论；
- 它的未来收益排序不靠前，被更强的信号排在摘要前面。

阅读这块内容时可以按三步走：

```text
第一步：看覆盖情况，确认一买 / 二买 / 三买各出现了多少次。
第二步：看未来10根平均收益，判断这个信号出现后是否真的有正向优势。
第三步：看胜率和样本数，避免被少数几次好运气误导。
```

后续我们会用这些统计结果提出假设，例如：

```text
30分钟三买只有在日线偏强时才有效？
二买比三买更适合震荡市？
60分钟出现卖点后，30分钟买点是否应该降权？
ETF 和个股的信号统计是否明显不同？
```

## 网页使用方式

启动本地网页：

```powershell
cd C:\Users\zhanc\Documents\GitHub\-
D:\Anaconda\python.exe scripts\chan_strategy_system\ui.py
```

然后打开：

```text
http://127.0.0.1:8765
```

使用步骤：

```text
1. 输入股票或 ETF 代码，例如 002202.SZ 或 510300.SH
2. 选择分析版本
3. 选择数据模式
4. V2 可选择 `快速巡检` 或 `完整研究`
5. 如有需要，调整高级参数
6. 点击开始分析
7. 等待进度条完成
8. 在右侧查看结果
```

版本选择：

- `V2 主版本底座`：默认选项，用于结构分析和信号研究。
- `V1 三买回测`：用于查看原始三买策略回测曲线。

界面上：

- V2 模式只显示 `V2多级别分析周期`；
- V1 模式只显示 `V1回测周期`；
- 这样可以避免把“交易执行周期”和“多级别研究周期”混在一起。

## 命令行运行方式

V2 Core：

```powershell
D:\Anaconda\python.exe -u scripts\chan_strategy_system\chan_core.py --mode tushare --symbols 002202.SZ --start-date 20200101 --end-date 20260612 --backtest-start 20200701 --analysis-freqs 日线,60分钟,30分钟 --fq 后复权
```

V2 快速巡检：

```powershell
D:\Anaconda\python.exe -u scripts\chan_strategy_system\chan_core.py --mode tushare --symbols 002202.SZ --start-date 20200101 --end-date 20260612 --backtest-start 20200701 --analysis-freqs 日线,60分钟,30分钟 --speed-mode quick --fq 后复权
```

V2 批量完整研究，以沪深300当前成分股为例：

```powershell
D:\Anaconda\python.exe -u scripts\chan_strategy_system\batch_v2.py --mode tushare --pool hs300 --start-date 20230101 --end-date 20260612 --backtest-start 20240901 --analysis-freqs 日线,60分钟,30分钟 --speed-mode standard --fq 前复权
```

如果只是先测试链路，可以加 `--limit 5` 只跑前 5 只：

```powershell
D:\Anaconda\python.exe -u scripts\chan_strategy_system\batch_v2.py --mode tushare --pool hs300 --start-date 20230101 --end-date 20260612 --backtest-start 20240901 --analysis-freqs 日线,60分钟,30分钟 --speed-mode standard --fq 前复权 --limit 5
```

批量研究会生成一个 `v2_batch_YYYYMMDD_HHMMSS` 目录，里面包含：

- `batch_summary.csv`：每只股票是否完成、评分、评级和子报告目录；
- `batch_signal_stats.csv`：所有股票的候选信号统计，用于后续假设检验；
- `batch_candidates.csv`：所有股票的候选买卖点明细；
- `batch_latest_states.csv`：所有股票的多级别最新结构；
- `batch_report.md`：批量研究摘要。

它会对每只股票写入 `progress/*.json` 进度标记。默认支持断点续跑；如果中途关闭终端，再运行同一条命令，会跳过已经完成的股票。若要强制重跑，追加 `--rerun`。

如果电脑关机或终端中断，建议下次用 `--resume-dir` 明确接着旧批量目录跑：

```powershell
D:\Anaconda\python.exe -u scripts\chan_strategy_system\batch_v2.py --mode tushare --pool hs300 --start-date 20230101 --end-date 20260612 --backtest-start 20240901 --analysis-freqs 日线,60分钟,30分钟 --speed-mode standard --fq 前复权 --resume-dir C:\Users\zhanc\Documents\GitHub\-\data\reports\chan_strategy\v2_batch_YYYYMMDD_HHMMSS
```

其中 `v2_batch_YYYYMMDD_HHMMSS` 换成你实际的批量目录名。脚本会读取该目录下的 `progress/*.json`，跳过已经完成的股票，只继续未完成或失败的股票。若你不传 `--resume-dir`，脚本会创建一个新的批量目录。

批量跑完后，可以做第一轮分层筛选分析：

```powershell
D:\Anaconda\python.exe scripts\chan_strategy_system\analyze_v2_filters.py --min-count 300
```

它会读取最新 `v2_batch_*` 和已完成的单票 V2 报告，生成 `filter_analysis_YYYYMMDD_HHMMSS` 目录。重点输出：

- `filter_analysis_report.md`：分层筛选摘要；
- `freq_signal.csv`：周期 + 信号类型的表现排序；
- `buy_big_not_weak.csv`：大级别不弱时买点是否改善；
- `buy_daily_trend.csv` / `buy_hour_trend.csv`：日线、60分钟趋势过滤效果；
- `buy_score_bucket.csv`：评分档位过滤效果；
- `sample_signal_stats_enriched.csv`：带趋势和评分字段的完整明细。

如果要进一步研究“在什么市场环境下缠论更有效”，可以生成事件级样本：

```powershell
D:\Anaconda\python.exe scripts\chan_strategy_system\analyze_v2_events.py --min-events 200
```

它会生成 `event_analysis_YYYYMMDD_HHMMSS` 目录。这个分析把每一次一买、二买、三买、一卖、二卖都当作独立事件，并补充：

- 信号发生后的未来收益；
- 样本池等权市场环境：上升 / 震荡 / 下跌；
- 个股20根动量、是否站上20均线、波动率；
- 更大级别是否不弱；
- 信号前10根内是否出现过卖点。

主要输出：

- `event_samples.csv`：事件级研究样本；
- `signal_by_freq_type.csv`：周期 + 信号类型统计；
- `buy_by_market_regime.csv`：市场环境下买点表现；
- `buy_by_higher_level.csv`：更大级别过滤效果；
- `buy_by_stock_state.csv`：个股状态过滤效果；
- `event_analysis_report.md`：事件级摘要。

V1 三买回测：

```powershell
D:\Anaconda\python.exe -u scripts\chan_strategy_system\backtest.py --mode tushare --pool custom --symbols 002202.SZ --start-date 20200101 --end-date 20260612 --backtest-start 20200701 --base-freq 30分钟 --fq 后复权 --limit 1
```

Mock 链路测试，不依赖 Tushare：

```powershell
D:\Anaconda\python.exe scripts\chan_strategy_system\chan_core.py --mode mock --symbols 002202.SZ --start-date 20200101 --end-date 20210101 --backtest-start 20200701 --analysis-freqs 60分钟,30分钟
```

Mock 只用于验证代码链路，不代表真实市场结果。

## Tushare 配置

不要把 token 写进源码，也不要提交到 Git。

在 PowerShell 里设置：

```powershell
$env:TUSHARE_TOKEN="your_token"
$env:TUSHARE_HTTP_URL="https://tt.dailyfetch.top/"
```

检查环境：

```powershell
D:\Anaconda\python.exe scripts\chan_strategy_system\check_env.py --network-check
```

判断真实数据是否成功，日志里应该看到：

```text
Real Tushare data used: True
Sample data used/generated: False
```

如果不是这两行，就不能把结果当作真实数据研究结果。

## 缓存和输出目录

缓存目录：

```text
data/cache/chan_strategy/
```

输出目录：

```text
data/reports/chan_strategy/
```

首次运行某只股票和某个周期时，会拉取并缓存 K 线，所以比较慢。后续如果参数不变，会复用缓存，速度会明显加快。

## 后续迭代路线

我们接下来不应该追求一次性写一个非常复杂的“完整策略”，而是基于 V2 Core 做可检验的实验。

建议路线：

```text
实验 A：V1 单周期三买基准
实验 B：三买 + 大级别偏强过滤
实验 C：三买 + 小级别止跌确认
实验 D：二买 + 三买组合
实验 E：买点系统 + 卖点退出
实验 F：买点系统 + 指数环境过滤
实验 G：持仓风险管理与次日操作建议
```

每个实验都应该和基准版本比较：

- 交易次数；
- 胜率；
- 平均收益；
- 盈亏比；
- 最大回撤；
- 连续亏损；
- 样本外表现；
- 实盘是否容易执行。

这个系统最终要服务的不是“自动满仓买卖”，而是：

```text
帮你看清当前持仓结构
帮你识别风险位
帮你判断信号是否有历史优势
帮你减少冲动交易
帮你形成可复盘的交易流程
```

换句话说，它是一个纪律化交易辅助系统，而不是一个承诺稳赚的预测机器。
