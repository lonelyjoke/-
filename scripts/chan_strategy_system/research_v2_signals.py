"""Create the first formal V2 signal research report from batch outputs.

This script is intentionally based on aggregated batch CSV files, not on a
fresh CZSC recomputation. It is designed as the first research layer:
data audit, naked signal benchmark, frequency attribution, and robustness
checks across symbols.
"""

from __future__ import annotations

import argparse
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_ROOT = REPO_ROOT / "data" / "reports" / "chan_strategy"
HORIZONS = (1, 3, 5, 10, 20)
FREQ_ORDER = {"日线": 0, "60分钟": 1, "30分钟": 2, "15分钟": 3}
SIGNAL_ORDER = {"一买": 0, "二买": 1, "三买": 2, "一卖": 3, "二卖": 4, "三卖": 5}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build V2 formal signal research report.")
    parser.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--batch-dir", help="Specific v2_batch_* directory. Default: latest.")
    parser.add_argument("--output-dir", help="Default: report-root/research_v2_signals_TIMESTAMP")
    parser.add_argument("--min-sample", type=int, default=100, help="Minimum aggregated samples for group tables.")
    return parser.parse_args()


def latest_batch(root: Path) -> Path:
    batches = sorted([p for p in root.glob("v2_batch_*") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if not batches:
        raise FileNotFoundError(f"No v2_batch_* directory found under {root}")
    return batches[0]


def resolve_batch_dir(report_root: Path, raw: str | None) -> Path:
    if not raw:
        return latest_batch(report_root)
    path = Path(raw)
    if path.is_absolute():
        return path
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path
    root_path = report_root / path
    if root_path.exists():
        return root_path
    return root_path


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def pct(value: float | int | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:.{digits}f}%"


def num(value: float | int | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def sort_key(row: pd.Series | dict) -> tuple:
    freq = row.get("freq", "")
    sig = row.get("signal_type", "")
    return (FREQ_ORDER.get(freq, 99), SIGNAL_ORDER.get(sig, 99), str(freq), str(sig))


def weighted_group(
    stats: pd.DataFrame,
    group_cols: list[str],
    *,
    horizons: tuple[int, ...] = HORIZONS,
    min_sample: int = 0,
) -> pd.DataFrame:
    rows = []
    if stats.empty:
        return pd.DataFrame()

    for keys, group in stats.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        row["rows"] = len(group)
        row["symbols"] = group["symbol"].nunique() if "symbol" in group.columns else None
        max_sample = 0

        for horizon in horizons:
            sample_col = f"ret_{horizon}b_sample"
            mean_col = f"ret_{horizon}b_mean"
            win_col = f"ret_{horizon}b_win_rate"
            valid = group[[sample_col, mean_col, win_col]].copy()
            valid[sample_col] = pd.to_numeric(valid[sample_col], errors="coerce")
            valid[mean_col] = pd.to_numeric(valid[mean_col], errors="coerce")
            valid[win_col] = pd.to_numeric(valid[win_col], errors="coerce")
            valid = valid.dropna()
            valid = valid[valid[sample_col] > 0]

            sample = float(valid[sample_col].sum()) if not valid.empty else 0.0
            max_sample = max(max_sample, int(sample))
            if sample > 0:
                row[f"ret_{horizon}b_sample"] = int(sample)
                row[f"ret_{horizon}b_mean"] = float((valid[mean_col] * valid[sample_col]).sum() / sample)
                row[f"ret_{horizon}b_win_rate"] = float((valid[win_col] * valid[sample_col]).sum() / sample)
            else:
                row[f"ret_{horizon}b_sample"] = 0
                row[f"ret_{horizon}b_mean"] = math.nan
                row[f"ret_{horizon}b_win_rate"] = math.nan

        if max_sample >= min_sample:
            rows.append(row)

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(by=group_cols, key=lambda s: s.map(lambda x: FREQ_ORDER.get(x, SIGNAL_ORDER.get(x, 99)) if isinstance(x, str) else x))
    return out.reset_index(drop=True)


def robustness_by_symbol(stats: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if stats.empty:
        return pd.DataFrame()

    for (freq, signal_type), group in stats.groupby(["freq", "signal_type"], dropna=False):
        values = pd.to_numeric(group["ret_10b_mean"], errors="coerce")
        wins = pd.to_numeric(group["ret_10b_win_rate"], errors="coerce")
        samples = pd.to_numeric(group["ret_10b_sample"], errors="coerce")
        valid = pd.DataFrame({"ret": values, "win": wins, "sample": samples}).dropna()
        valid = valid[valid["sample"] > 0]
        if valid.empty:
            continue
        rows.append(
            {
                "freq": freq,
                "signal_type": signal_type,
                "symbols": len(valid),
                "positive_symbol_rate": float((valid["ret"] > 0).mean()),
                "median_ret_10b": float(valid["ret"].median()),
                "mean_ret_10b_unweighted": float(valid["ret"].mean()),
                "median_win_rate_10b": float(valid["win"].median()),
                "median_sample": float(valid["sample"].median()),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out["_sort"] = out.apply(sort_key, axis=1)
        out = out.sort_values("_sort").drop(columns="_sort")
    return out.reset_index(drop=True)


def candidate_counts(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    out = (
        candidates.groupby(["freq", "signal_type"], dropna=False)
        .agg(events=("symbol", "size"), symbols=("symbol", "nunique"))
        .reset_index()
    )
    out["_sort"] = out.apply(sort_key, axis=1)
    return out.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)


def latest_state_summary(states: pd.DataFrame) -> pd.DataFrame:
    if states.empty:
        return pd.DataFrame()
    rows = []
    for freq, group in states.groupby("freq", dropna=False):
        counts = Counter(group["trend"].fillna("未知").astype(str))
        total = sum(counts.values())
        row = {"freq": freq, "symbols": group["symbol"].nunique()}
        for key in ["偏强", "震荡", "偏弱", "未知"]:
            row[key] = counts.get(key, 0)
            row[f"{key}_rate"] = counts.get(key, 0) / total if total else math.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("freq", key=lambda s: s.map(lambda x: FREQ_ORDER.get(x, 99)))
    return out.reset_index(drop=True)


def top_groups(table: pd.DataFrame, horizon: int = 20, n: int = 8, min_sample: int = 1000) -> pd.DataFrame:
    if table.empty:
        return pd.DataFrame()
    sample_col = f"ret_{horizon}b_sample"
    mean_col = f"ret_{horizon}b_mean"
    out = table.copy()
    out = out[pd.to_numeric(out[sample_col], errors="coerce") >= min_sample]
    out = out.sort_values(mean_col, ascending=False)
    return out.head(n).reset_index(drop=True)


def table_to_markdown(df: pd.DataFrame, columns: list[str], rename: dict[str, str] | None = None, max_rows: int | None = None) -> str:
    if df.empty:
        return "暂无数据。"
    use = df[columns].copy()
    rename = rename or {}
    use = use.rename(columns=rename)
    if max_rows:
        use = use.head(max_rows)
    return use.to_markdown(index=False)


def add_pct_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if (
            col.endswith("_mean")
            or col.endswith("_win_rate")
            or col.endswith("_rate")
            or col in {"median_ret_10b", "mean_ret_10b_unweighted", "median_win_rate_10b"}
        ):
            out[col] = out[col].map(pct)
    return out


def write_report(
    output_dir: Path,
    batch_dir: Path,
    summary: pd.DataFrame,
    overall: pd.DataFrame,
    by_freq: pd.DataFrame,
    robustness: pd.DataFrame,
    candidates_count: pd.DataFrame,
    states_summary: pd.DataFrame,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "research_report.md"

    status_counts = Counter(summary["status"].fillna("未知").astype(str)) if not summary.empty else Counter()
    rating_counts = Counter(summary.get("rating", pd.Series(dtype=str)).fillna("未知").astype(str)) if not summary.empty else Counter()
    score = pd.to_numeric(summary.get("score", pd.Series(dtype=float)), errors="coerce") if not summary.empty else pd.Series(dtype=float)
    done_symbols = int((summary["status"] == "done").sum()) if "status" in summary.columns else len(summary)

    overall_fmt = add_pct_columns(overall)
    by_freq_fmt = add_pct_columns(by_freq)
    robustness_fmt = add_pct_columns(robustness)
    states_fmt = add_pct_columns(states_summary)
    best20 = add_pct_columns(top_groups(by_freq, horizon=20, min_sample=1000))
    best10 = add_pct_columns(top_groups(by_freq, horizon=10, min_sample=1000))

    lines = [
        "# 沪深300缠论信号第一阶段研究报告",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 研究定位",
        "",
        "本报告是 V2 Core 的第一份正式研究报告，目标是回答三个基础问题：",
        "",
        "1. 裸缠论信号是否具备直接交易价值；",
        "2. 日线、60分钟、30分钟在策略中应如何分工；",
        "3. 哪些信号组合值得进入下一阶段事件级和环境分组研究。",
        "",
        "本报告只使用已经生成的批量统计文件，不重新计算 CZSC 结构，也不重新抓取 Tushare 数据。",
        "",
        "## 2. 数据体检",
        "",
        f"- 批量目录：`{batch_dir}`",
        f"- 汇总股票数：`{len(summary)}`",
        f"- 完成股票数：`{done_symbols}`",
        f"- 状态分布：`{dict(status_counts)}`",
        f"- 评级分布：`{dict(rating_counts)}`",
        f"- 综合评分均值：`{num(score.mean(), 2)}`；中位数：`{num(score.median(), 2)}`；范围：`{num(score.min(), 1)} ~ {num(score.max(), 1)}`",
        "",
        "当前样本已覆盖沪深300全池。第一层统计可以用于建立研究假设，但正式交易规则仍需事件级样本、市场环境分组和样本外验证。",
        "",
        "## 3. 裸信号基准",
        "",
        table_to_markdown(
            overall_fmt,
            [
                "signal_type",
                "symbols",
                "ret_10b_sample",
                "ret_10b_mean",
                "ret_10b_win_rate",
                "ret_20b_sample",
                "ret_20b_mean",
                "ret_20b_win_rate",
            ],
            {
                "signal_type": "信号",
                "symbols": "覆盖股票",
                "ret_10b_sample": "10根样本",
                "ret_10b_mean": "10根均值",
                "ret_10b_win_rate": "10根胜率",
                "ret_20b_sample": "20根样本",
                "ret_20b_mean": "20根均值",
                "ret_20b_win_rate": "20根胜率",
            },
        ),
        "",
        "结论：裸信号整体平均收益为小正，但胜率大多低于 50%。这说明一买、二买、三买不是直接买入按钮，一卖、二卖也不是直接卖出按钮。它们更适合作为结构事件，再结合市场环境、个股状态和多周期联立做二次过滤。",
        "",
        "## 4. 周期与信号分工",
        "",
        table_to_markdown(
            by_freq_fmt,
            [
                "freq",
                "signal_type",
                "symbols",
                "ret_10b_sample",
                "ret_10b_mean",
                "ret_10b_win_rate",
                "ret_20b_mean",
                "ret_20b_win_rate",
            ],
            {
                "freq": "周期",
                "signal_type": "信号",
                "symbols": "覆盖股票",
                "ret_10b_sample": "10根样本",
                "ret_10b_mean": "10根均值",
                "ret_10b_win_rate": "10根胜率",
                "ret_20b_mean": "20根均值",
                "ret_20b_win_rate": "20根胜率",
            },
        ),
        "",
        "### 4.1 未来20根表现靠前的周期-信号组合",
        "",
        table_to_markdown(
            best20,
            ["freq", "signal_type", "symbols", "ret_20b_sample", "ret_20b_mean", "ret_20b_win_rate"],
            {
                "freq": "周期",
                "signal_type": "信号",
                "symbols": "覆盖股票",
                "ret_20b_sample": "20根样本",
                "ret_20b_mean": "20根均值",
                "ret_20b_win_rate": "20根胜率",
            },
        ),
        "",
        "### 4.2 未来10根表现靠前的周期-信号组合",
        "",
        table_to_markdown(
            best10,
            ["freq", "signal_type", "symbols", "ret_10b_sample", "ret_10b_mean", "ret_10b_win_rate"],
            {
                "freq": "周期",
                "signal_type": "信号",
                "symbols": "覆盖股票",
                "ret_10b_sample": "10根样本",
                "ret_10b_mean": "10根均值",
                "ret_10b_win_rate": "10根胜率",
            },
        ),
        "",
        "研究判断：日线和60分钟更适合作为主研究周期；30分钟信号数量最多，但单独使用时收益和胜率优势不明显，更适合作为更高周期信号成立后的入场细化工具。",
        "",
        "## 5. 横截面稳健性",
        "",
        table_to_markdown(
            robustness_fmt,
            [
                "freq",
                "signal_type",
                "symbols",
                "positive_symbol_rate",
                "median_ret_10b",
                "mean_ret_10b_unweighted",
                "median_win_rate_10b",
                "median_sample",
            ],
            {
                "freq": "周期",
                "signal_type": "信号",
                "symbols": "股票数",
                "positive_symbol_rate": "正收益股票占比",
                "median_ret_10b": "股票中位收益",
                "mean_ret_10b_unweighted": "股票等权收益",
                "median_win_rate_10b": "中位胜率",
                "median_sample": "单股中位样本",
            },
        ),
        "",
        "研究判断：如果一个信号的加权平均收益为正，但正收益股票占比或股票中位收益较弱，就说明它可能由少数强势股票贡献，不应直接进入规则。后续需要重点检查日线二买、日线三买等信号的横截面稳定性。",
        "",
        "## 6. 当前结构状态",
        "",
        table_to_markdown(
            states_fmt,
            ["freq", "symbols", "偏强", "偏强_rate", "震荡", "震荡_rate", "偏弱", "偏弱_rate"],
            {
                "freq": "周期",
                "symbols": "股票数",
                "偏强": "偏强数量",
                "偏强_rate": "偏强占比",
                "震荡": "震荡数量",
                "震荡_rate": "震荡占比",
                "偏弱": "偏弱数量",
                "偏弱_rate": "偏弱占比",
            },
        ),
        "",
        "这部分用于观察当前沪深300样本池的结构分布，不直接代表历史信号有效性。它更适合后续生成观察池和风险提示。",
        "",
        "## 7. 第一阶段可建立的研究假设",
        "",
        "### 假设 H1：裸买点没有直接交易优势",
        "",
        "依据：一买、二买、三买在全样本下平均收益小正，但胜率接近随机。后续应验证环境过滤和个股状态过滤是否显著改善信号质量。",
        "",
        "### 假设 H2：60分钟三买/一买可能比30分钟买点更适合作为触发信号",
        "",
        "依据：60分钟信号的20根收益表现优于30分钟裸信号。后续应验证日线不弱条件下，60分钟买点是否进一步改善。",
        "",
        "### 假设 H3：30分钟信号应作为入场细化，而非独立开仓依据",
        "",
        "依据：30分钟信号样本最多，但裸胜率和收益优势有限。后续应检验“日线/60分钟条件成立 + 30分钟买点”是否优于单独30分钟买点。",
        "",
        "### 假设 H4：一卖、二卖不是简单清仓信号",
        "",
        "依据：日线一卖、二卖后续收益并不弱，尤其20根维度仍有较高正向收益。后续应研究卖点在不同市场环境中的风控用途。",
        "",
        "### 假设 H5：信号有效性可能高度依赖市场环境和个股状态",
        "",
        "依据：裸信号胜率不足，但平均收益存在正漂移。后续应把信号拆到上升、震荡、下跌环境，以及个股强弱、波动、位置状态中验证。",
        "",
        "## 8. 下一步研究路线",
        "",
        "1. 修复并优化完整事件级分析，生成 300 只全样本 `event_samples.csv`；",
        "2. 对 H1-H5 分别做分组验证；",
        "3. 优先研究“日线不弱 + 60分钟一买/三买”和“高周期成立 + 30分钟入场细化”；",
        "4. 单独研究一卖、二卖在强市与弱市中的不同含义；",
        "5. 只将跨周期、跨股票、跨时间都相对稳定的结论纳入 V3 规则。",
        "",
        "## 9. 研究员备注",
        "",
        "本阶段最重要的发现不是某个信号收益最高，而是确认了研究方向：缠论信号需要从“单点买卖”转化为“结构事件 + 环境过滤 + 多级别确认”。后续策略成败，关键在于能否找到稳定、简单、可解释的过滤条件。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run(args: argparse.Namespace) -> Path:
    report_root = Path(args.report_root)
    batch_dir = resolve_batch_dir(report_root, args.batch_dir)
    output_dir = Path(args.output_dir) if args.output_dir else report_root / ("research_v2_signals_" + datetime.now().strftime("%Y%m%d_%H%M%S"))

    summary = read_csv(batch_dir / "batch_summary.csv")
    stats = read_csv(batch_dir / "batch_signal_stats.csv")
    candidates = read_csv(batch_dir / "batch_candidates.csv")
    states = read_csv(batch_dir / "batch_latest_states.csv")

    if summary.empty or stats.empty:
        raise FileNotFoundError(f"Missing batch_summary.csv or batch_signal_stats.csv in {batch_dir}")

    overall = weighted_group(stats, ["signal_type"], min_sample=args.min_sample)
    by_freq = weighted_group(stats, ["freq", "signal_type"], min_sample=args.min_sample)
    robustness = robustness_by_symbol(stats)
    counts = candidate_counts(candidates)
    state_summary = latest_state_summary(states)

    output_dir.mkdir(parents=True, exist_ok=True)
    overall.to_csv(output_dir / "overall_signal_stats.csv", index=False, encoding="utf-8-sig")
    by_freq.to_csv(output_dir / "freq_signal_stats.csv", index=False, encoding="utf-8-sig")
    robustness.to_csv(output_dir / "signal_robustness_by_symbol.csv", index=False, encoding="utf-8-sig")
    counts.to_csv(output_dir / "candidate_counts.csv", index=False, encoding="utf-8-sig")
    state_summary.to_csv(output_dir / "latest_state_summary.csv", index=False, encoding="utf-8-sig")

    report = write_report(output_dir, batch_dir, summary, overall, by_freq, robustness, counts, state_summary)
    print(f"[done] research report: {report}")
    return report


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
