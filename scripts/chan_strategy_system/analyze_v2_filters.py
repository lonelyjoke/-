"""Analyze V2 signal filters on existing reports.

The script reads the latest V2 batch report plus completed single-symbol V2
reports, then builds exploratory filter tables for hypothesis generation. It
does not fetch market data and does not change strategy logic.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_ROOT = REPO_ROOT / "data" / "reports" / "chan_strategy"
BUY_SIGNALS = {"一买", "二买", "三买"}
SELL_SIGNALS = {"一卖", "二卖", "三卖"}


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze V2 filter layers from existing reports.")
    parser.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--batch-dir", help="Specific v2_batch_* directory. Default: latest.")
    parser.add_argument("--include-singles", action="store_true", default=True)
    parser.add_argument("--output-dir", help="Default: report-root/filter_analysis_TIMESTAMP")
    parser.add_argument("--min-count", type=int, default=300)
    return parser.parse_args()


def _latest_batch(root: Path) -> Path:
    batches = sorted([p for p in root.glob("v2_batch_*") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if not batches:
        raise FileNotFoundError(f"No v2_batch_* directory found under {root}")
    return batches[0]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_sample(root: Path, batch_dir: Path, include_singles: bool) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_frames = []
    state_frames = []
    stat_frames = []

    batch_summary = _read_csv(batch_dir / "batch_summary.csv")
    batch_states = _read_csv(batch_dir / "batch_latest_states.csv")
    batch_stats = _read_csv(batch_dir / "batch_signal_stats.csv")
    if not batch_summary.empty:
        batch_summary["source"] = "batch"
        summary_frames.append(batch_summary)
    if not batch_states.empty:
        batch_states["source"] = "batch"
        state_frames.append(batch_states)
    if not batch_stats.empty:
        batch_stats["source"] = "batch"
        stat_frames.append(batch_stats)

    batch_symbols = set(batch_summary["symbol"].astype(str)) if not batch_summary.empty else set()
    if include_singles:
        for run_dir in sorted(root.glob("v2_*"), key=lambda p: p.stat().st_mtime):
            if not run_dir.is_dir() or run_dir.name.startswith("v2_batch_"):
                continue
            cfg = _read_json(run_dir / "v2_config.json")
            summary = _read_json(run_dir / "v2_summary.json")
            states = _read_csv(run_dir / "v2_latest_states.csv")
            stats = _read_csv(run_dir / "v2_signal_stats.csv")
            symbol = cfg.get("symbols", "")
            if not symbol or symbol in batch_symbols or not summary or states.empty or stats.empty:
                continue
            summary_frames.append(
                pd.DataFrame(
                    [
                        {
                            "symbol": symbol,
                            "status": "done",
                            "score": summary.get("score"),
                            "rating": summary.get("rating"),
                            "action": summary.get("action"),
                            "reason": summary.get("reason"),
                            "source": "single",
                        }
                    ]
                )
            )
            states["source"] = "single"
            stats["source"] = "single"
            state_frames.append(states)
            stat_frames.append(stats)

    summary = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    states = pd.concat(state_frames, ignore_index=True) if state_frames else pd.DataFrame()
    stats = pd.concat(stat_frames, ignore_index=True) if stat_frames else pd.DataFrame()
    return summary, states, stats


def normalize(summary: pd.DataFrame, states: pd.DataFrame, stats: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not summary.empty:
        summary["score"] = pd.to_numeric(summary.get("score"), errors="coerce")
        summary["score_bucket"] = pd.cut(
            summary["score"],
            bins=[-1, 39, 59, 74, 100],
            labels=["低分<40", "中性40-59", "偏强60-74", "强势>=75"],
        ).astype(str)
    if not states.empty:
        states["freq"] = states["freq"].astype(str)
        states["trend"] = states["trend"].astype(str)
        states["bi_head"] = states["bi_status"].astype(str).str.split("_").str[0]
    if not stats.empty:
        for col in stats.columns:
            if col == "count" or col.startswith("ret_"):
                stats[col] = pd.to_numeric(stats[col], errors="coerce")
        stats["is_buy"] = stats["signal_type"].isin(BUY_SIGNALS)
        stats["is_sell"] = stats["signal_type"].isin(SELL_SIGNALS)
    return summary, states, stats


def enrich_stats(summary: pd.DataFrame, states: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    enriched = stats.copy()
    if summary.empty or enriched.empty:
        return enriched
    enriched = enriched.merge(summary[["symbol", "score", "score_bucket", "rating"]], on="symbol", how="left")
    state_wide = states.pivot_table(index="symbol", columns="freq", values="trend", aggfunc="last")
    state_wide = state_wide.rename(columns={col: f"{col}_trend" for col in state_wide.columns}).reset_index()
    enriched = enriched.merge(state_wide, on="symbol", how="left")
    enriched["big_not_weak"] = enriched.get("日线_trend", "").ne("偏弱") & enriched.get("60分钟_trend", "").ne("偏弱")
    enriched["big_strong"] = enriched.get("日线_trend", "").eq("偏强") | enriched.get("60分钟_trend", "").eq("偏强")
    enriched["daily_not_weak"] = enriched.get("日线_trend", "").ne("偏弱")
    enriched["hour_not_weak"] = enriched.get("60分钟_trend", "").ne("偏弱")
    enriched["small_not_weak"] = enriched.get("30分钟_trend", "").ne("偏弱")
    return enriched


def weighted_group(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        row["symbols"] = group["symbol"].nunique()
        row["rows"] = len(group)
        row["count_sum"] = int(group["count"].fillna(0).sum())
        for horizon in [5, 10, 20]:
            mean_col = f"ret_{horizon}b_mean"
            win_col = f"ret_{horizon}b_win_rate"
            sample_col = f"ret_{horizon}b_sample"
            weights = group[sample_col].fillna(group["count"]).clip(lower=0) if sample_col in group else group["count"].clip(lower=0)
            mean_valid = group[mean_col].notna() & weights.gt(0)
            win_valid = group[win_col].notna() & weights.gt(0)
            row[f"ret_{horizon}b_mean"] = (
                float((group.loc[mean_valid, mean_col] * weights.loc[mean_valid]).sum() / weights.loc[mean_valid].sum())
                if mean_valid.any()
                else None
            )
            row[f"ret_{horizon}b_win_rate"] = (
                float((group.loc[win_valid, win_col] * weights.loc[win_valid]).sum() / weights.loc[win_valid].sum())
                if win_valid.any()
                else None
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _fmt_pct(value) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.2%}"


def build_filter_tables(enriched: pd.DataFrame, min_count: int) -> dict[str, pd.DataFrame]:
    buy = enriched[enriched["is_buy"]].copy()
    sell = enriched[enriched["is_sell"]].copy()
    tables = {
        "signal_type": weighted_group(enriched, ["signal_type"]).sort_values("ret_10b_mean", ascending=False),
        "freq_signal": weighted_group(enriched, ["freq", "signal_type"]).sort_values("ret_10b_mean", ascending=False),
        "buy_big_not_weak": weighted_group(buy, ["big_not_weak", "signal_type"]).sort_values("ret_10b_mean", ascending=False),
        "buy_daily_trend": weighted_group(buy, ["日线_trend", "signal_type"]).sort_values("ret_10b_mean", ascending=False),
        "buy_hour_trend": weighted_group(buy, ["60分钟_trend", "signal_type"]).sort_values("ret_10b_mean", ascending=False),
        "buy_small_trend": weighted_group(buy, ["30分钟_trend", "signal_type"]).sort_values("ret_10b_mean", ascending=False),
        "buy_score_bucket": weighted_group(buy, ["score_bucket", "signal_type"]).sort_values("ret_10b_mean", ascending=False),
        "sell_big_not_weak": weighted_group(sell, ["big_not_weak", "signal_type"]).sort_values("ret_10b_mean", ascending=True),
    }
    tables["candidate_filters"] = (
        tables["buy_big_not_weak"]
        .query("count_sum >= @min_count")
        .sort_values(["ret_10b_mean", "ret_10b_win_rate"], ascending=False)
        .reset_index(drop=True)
    )
    return tables


def write_outputs(output_dir: Path, summary: pd.DataFrame, states: pd.DataFrame, enriched: pd.DataFrame, tables: dict[str, pd.DataFrame], min_count: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_dir / "sample_summary.csv", index=False, encoding="utf-8-sig")
    states.to_csv(output_dir / "sample_latest_states.csv", index=False, encoding="utf-8-sig")
    enriched.to_csv(output_dir / "sample_signal_stats_enriched.csv", index=False, encoding="utf-8-sig")
    for name, table in tables.items():
        table.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8-sig")

    report = output_dir / "filter_analysis_report.md"
    lines = [
        "# V2 小样本分层筛选分析",
        "",
        "## 样本说明",
        "",
        f"- 股票数量: `{summary['symbol'].nunique() if not summary.empty else 0}`",
        f"- 分层最小样本次数: `{min_count}`",
        "- 本报告用于提出候选假设，不是正式交易规则。",
        "",
        "## 核心结论",
        "",
    ]
    candidate = tables.get("candidate_filters", pd.DataFrame())
    if candidate.empty:
        lines.append("- 当前没有满足最小样本次数的明显增强分层。")
    else:
        for _, row in candidate.head(5).iterrows():
            lines.append(
                f"- `{row['signal_type']}` 在 `big_not_weak={row['big_not_weak']}` 条件下："
                f"样本 {int(row['count_sum'])} 次，未来10根平均收益 {_fmt_pct(row['ret_10b_mean'])}，"
                f"胜率 {_fmt_pct(row['ret_10b_win_rate'])}。"
            )
    lines.extend(["", "## 分周期信号排名", ""])
    freq_signal = tables["freq_signal"].query("count_sum >= @min_count").head(12)
    if not freq_signal.empty:
        lines.extend(["```text", freq_signal.to_string(index=False), "```"])
    lines.extend(["", "## 研究员读法", ""])
    lines.append("- 优先观察收益和胜率同时改善、且样本数不太小的组合。")
    lines.append("- 如果过滤后收益改善但胜率仍低，说明它可能偏赔率型，需要后续配合止损/止盈验证。")
    lines.append("- 如果日线或60分钟偏弱时信号明显恶化，应作为后续规则的过滤条件。")
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    args = parse_args()
    root = Path(args.report_root)
    batch_dir = Path(args.batch_dir) if args.batch_dir else _latest_batch(root)
    if not batch_dir.is_absolute():
        batch_dir = root / batch_dir
    output_dir = Path(args.output_dir) if args.output_dir else root / ("filter_analysis_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    summary, states, stats = load_sample(root, batch_dir, args.include_singles)
    summary, states, stats = normalize(summary, states, stats)
    enriched = enrich_stats(summary, states, stats)
    tables = build_filter_tables(enriched, args.min_count)
    report = write_outputs(output_dir, summary, states, enriched, tables, args.min_count)
    print(f"[done] filter report: {report}")


if __name__ == "__main__":
    main()
