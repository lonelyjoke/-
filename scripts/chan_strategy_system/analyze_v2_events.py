"""Build event-level research samples from completed V2 reports.

This module turns V2 candidate signals into one-row-per-event samples and
labels each event with stock state, sample-market regime, and higher-level
context. It does not recompute CZSC signals and does not fetch data.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_ROOT = REPO_ROOT / "data" / "reports" / "chan_strategy"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "chan_strategy"
BUY_SIGNALS = {"一买", "二买", "三买"}
SELL_SIGNALS = {"一卖", "二卖", "三卖"}
FREQ_DIRS = {"日线": "_", "60分钟": "60_", "30分钟": "30_", "15分钟": "15_"}
HORIZONS = (1, 3, 5, 10, 20)


def parse_args():
    parser = argparse.ArgumentParser(description="Create event-level V2 analysis from existing reports.")
    parser.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--batch-dir", help="Specific v2_batch_* directory. Default: latest.")
    parser.add_argument("--output-dir", help="Default: report-root/event_analysis_TIMESTAMP")
    parser.add_argument("--min-events", type=int, default=200)
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


def load_reports(root: Path, batch_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load completed batch + completed single V2 candidates and summaries."""
    summary_frames = []
    candidate_frames = []

    batch_summary = _read_csv(batch_dir / "batch_summary.csv")
    batch_candidates = _read_csv(batch_dir / "batch_candidates.csv")
    if not batch_summary.empty:
        batch_summary["source"] = "batch"
        summary_frames.append(batch_summary)
    if not batch_candidates.empty:
        batch_candidates["source"] = "batch"
        candidate_frames.append(batch_candidates)
    batch_symbols = set(batch_summary["symbol"].astype(str)) if not batch_summary.empty else set()

    for run_dir in sorted(root.glob("v2_*"), key=lambda p: p.stat().st_mtime):
        if not run_dir.is_dir() or run_dir.name.startswith("v2_batch_"):
            continue
        cfg = _read_json(run_dir / "v2_config.json")
        summary = _read_json(run_dir / "v2_summary.json")
        candidates = _read_csv(run_dir / "v2_candidates.csv")
        states = _read_csv(run_dir / "v2_latest_states.csv")
        symbol = cfg.get("symbols", "")
        if not symbol or symbol in batch_symbols or not summary or candidates.empty or states.empty:
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
        candidates["source"] = "single"
        candidate_frames.append(candidates)

    summary_df = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    candidates_df = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
    if not candidates_df.empty:
        candidates_df["dt"] = pd.to_datetime(candidates_df["dt"])
        candidates_df = candidates_df.drop_duplicates(["symbol", "freq", "dt", "signal_type", "signal_key"]).reset_index(drop=True)
    return summary_df, candidates_df


def load_bars(cache_dir: Path, symbol: str, freq: str) -> pd.DataFrame:
    freq_dir = cache_dir / "bars" / FREQ_DIRS.get(freq, freq)
    if not freq_dir.exists():
        return pd.DataFrame()
    frames = []
    for path in sorted(freq_dir.glob(f"{symbol}_*.parquet")):
        try:
            frames.append(pd.read_parquet(path))
        except Exception:  # noqa: BLE001
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["dt"] = pd.to_datetime(df["dt"])
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.drop_duplicates("dt").sort_values("dt").reset_index(drop=True)


def label_momentum(value: float | None, *, strong: float = 0.05, weak: float = -0.03) -> str:
    if value is None or pd.isna(value):
        return "未知"
    if value >= strong:
        return "强"
    if value <= weak:
        return "弱"
    return "中"


def prepare_bar_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy().sort_values("dt").reset_index(drop=True)
    out["bar_index"] = range(len(out))
    ret1 = out["close"].pct_change()
    out["mom20"] = out["close"] / out["close"].shift(20) - 1
    out["mom60"] = out["close"] / out["close"].shift(60) - 1
    out["vol20"] = ret1.rolling(20).std()
    out["ma20"] = out["close"].rolling(20).mean()
    out["above_ma20"] = out["close"] >= out["ma20"]
    out["high20"] = out["high"].rolling(20).max()
    out["low20"] = out["low"].rolling(20).min()
    out["near_high20"] = out["close"] >= out["high20"] * 0.98
    out["near_low20"] = out["close"] <= out["low20"] * 1.02
    for horizon in HORIZONS:
        out[f"ret_{horizon}b"] = out["close"].shift(-horizon) / out["close"] - 1
    vol_median = out["vol20"].median()
    out["mom20_bucket"] = out["mom20"].map(label_momentum)
    out["mom60_bucket"] = out["mom60"].map(lambda x: label_momentum(x, strong=0.10, weak=-0.06))
    out["vol20_bucket"] = out["vol20"].map(lambda x: "高" if pd.notna(x) and x >= vol_median else ("低" if pd.notna(x) else "未知"))
    return out


def build_market_regime(cache_dir: Path, symbols: list[str]) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        bars = load_bars(cache_dir, symbol, "日线")
        if bars.empty:
            continue
        features = prepare_bar_features(bars)
        keep = features[["dt", "symbol", "mom20", "above_ma20", "vol20"]].copy()
        keep["date"] = keep["dt"].dt.normalize()
        frames.append(keep)
    if not frames:
        return pd.DataFrame(columns=["date", "market_ret20", "market_breadth", "market_vol20", "market_regime"])
    panel = pd.concat(frames, ignore_index=True)
    market = (
        panel.groupby("date")
        .agg(
            market_ret20=("mom20", "mean"),
            market_breadth=("above_ma20", "mean"),
            market_vol20=("vol20", "median"),
            market_symbols=("symbol", "nunique"),
        )
        .reset_index()
        .sort_values("date")
    )
    market["market_regime"] = market.apply(_market_regime, axis=1)
    market["market_momentum"] = market["market_ret20"].map(lambda x: label_momentum(x, strong=0.04, weak=-0.02))
    market["market_breadth_bucket"] = market["market_breadth"].map(
        lambda x: "强宽度" if pd.notna(x) and x >= 0.6 else ("弱宽度" if pd.notna(x) and x <= 0.4 else "中性宽度")
    )
    return market


def _market_regime(row: pd.Series) -> str:
    ret20 = row.get("market_ret20")
    breadth = row.get("market_breadth")
    if pd.isna(ret20) or pd.isna(breadth):
        return "未知"
    if ret20 > 0.03 and breadth >= 0.60:
        return "上升"
    if ret20 < -0.02 and breadth <= 0.40:
        return "下跌"
    return "震荡"


def build_event_samples(candidates: pd.DataFrame, cache_dir: Path) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    symbols = sorted(candidates["symbol"].astype(str).unique().tolist())
    market = build_market_regime(cache_dir, symbols)
    feature_cache: dict[tuple[str, str], pd.DataFrame] = {}
    event_frames = []

    for (symbol, freq), group in candidates.groupby(["symbol", "freq"]):
        bars = load_bars(cache_dir, symbol, freq)
        if bars.empty:
            continue
        features = prepare_bar_features(bars)
        feature_cache[(symbol, freq)] = features
        cols = [
            "dt",
            "bar_index",
            "close",
            "mom20",
            "mom60",
            "mom20_bucket",
            "mom60_bucket",
            "vol20",
            "vol20_bucket",
            "above_ma20",
            "near_high20",
            "near_low20",
            *[f"ret_{h}b" for h in HORIZONS],
        ]
        merged = group.merge(features[cols], on="dt", how="left", suffixes=("", "_bar"))
        event_frames.append(merged)

    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    if events.empty:
        return events
    events["date"] = pd.to_datetime(events["dt"]).dt.normalize()
    events["is_buy"] = events["signal_type"].isin(BUY_SIGNALS)
    events["is_sell"] = events["signal_type"].isin(SELL_SIGNALS)
    events = _add_market_labels(events, market)
    events = _add_higher_level_labels(events, feature_cache)
    events = _add_recent_sell_label(events)
    return events


def _add_market_labels(events: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    if market.empty:
        events["market_regime"] = "未知"
        events["market_momentum"] = "未知"
        events["market_breadth_bucket"] = "未知"
        return events
    events = events.sort_values("date").reset_index(drop=True)
    market = market.sort_values("date").reset_index(drop=True)
    return pd.merge_asof(events, market, on="date", direction="backward")


def _asof_feature(features: pd.DataFrame, dt: pd.Timestamp, prefix: str) -> dict:
    if features.empty:
        return {f"{prefix}_mom20_bucket": "未知", f"{prefix}_above_ma20": None}
    idx = features["dt"].searchsorted(dt, side="right") - 1
    if idx < 0:
        return {f"{prefix}_mom20_bucket": "未知", f"{prefix}_above_ma20": None}
    row = features.iloc[idx]
    return {
        f"{prefix}_mom20_bucket": row.get("mom20_bucket", "未知"),
        f"{prefix}_above_ma20": bool(row.get("above_ma20")) if pd.notna(row.get("above_ma20")) else None,
    }


def _add_higher_level_labels(events: pd.DataFrame, feature_cache: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for row in events.to_dict("records"):
        symbol = row["symbol"]
        dt = pd.to_datetime(row["dt"])
        daily = _asof_feature(feature_cache.get((symbol, "日线"), pd.DataFrame()), dt, "daily")
        hour = _asof_feature(feature_cache.get((symbol, "60分钟"), pd.DataFrame()), dt, "hour")
        row.update(daily)
        row.update(hour)
        if row["freq"] == "30分钟":
            row["higher_level_not_weak"] = daily["daily_mom20_bucket"] != "弱" and hour["hour_mom20_bucket"] != "弱"
        elif row["freq"] == "60分钟":
            row["higher_level_not_weak"] = daily["daily_mom20_bucket"] != "弱"
        else:
            row["higher_level_not_weak"] = row.get("market_regime") != "下跌"
        rows.append(row)
    return pd.DataFrame(rows)


def _add_recent_sell_label(events: pd.DataFrame) -> pd.DataFrame:
    out = events.sort_values(["symbol", "freq", "dt"]).copy()
    out["recent_sell_10b"] = False
    for _, idx in out.groupby(["symbol", "freq"]).groups.items():
        last_sell_bar = None
        for i in idx:
            bar_index = out.at[i, "bar_index"]
            if pd.notna(bar_index) and last_sell_bar is not None and bar_index - last_sell_bar <= 10:
                out.at[i, "recent_sell_10b"] = True
            if out.at[i, "signal_type"] in SELL_SIGNALS and pd.notna(bar_index):
                last_sell_bar = bar_index
    return out


def aggregate(events: pd.DataFrame, group_cols: list[str], min_events: int = 0) -> pd.DataFrame:
    rows = []
    for keys, group in events.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        row["events"] = len(group)
        row["symbols"] = group["symbol"].nunique()
        for horizon in [5, 10, 20]:
            col = f"ret_{horizon}b"
            valid = group[col].dropna()
            row[f"{col}_mean"] = float(valid.mean()) if not valid.empty else None
            row[f"{col}_win_rate"] = float((valid > 0).mean()) if not valid.empty else None
        rows.append(row)
    result = pd.DataFrame(rows)
    if min_events and not result.empty:
        result = result[result["events"] >= min_events]
    if "ret_10b_mean" in result.columns:
        result = result.sort_values(["ret_10b_mean", "ret_10b_win_rate"], ascending=False)
    return result.reset_index(drop=True)


def _fmt_pct(value) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.2%}"


def write_outputs(output_dir: Path, events: pd.DataFrame, min_events: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(output_dir / "event_samples.csv", index=False, encoding="utf-8-sig")
    tables = {
        "signal_by_freq_type": aggregate(events, ["freq", "signal_type"], min_events=min_events),
        "signal_by_market_regime": aggregate(events, ["market_regime", "freq", "signal_type"], min_events=min_events),
        "buy_by_market_regime": aggregate(events[events["is_buy"]], ["market_regime", "freq", "signal_type"], min_events=min_events),
        "buy_by_stock_state": aggregate(events[events["is_buy"]], ["mom20_bucket", "above_ma20", "signal_type"], min_events=min_events),
        "buy_by_higher_level": aggregate(events[events["is_buy"]], ["higher_level_not_weak", "freq", "signal_type"], min_events=min_events),
        "buy_by_recent_sell": aggregate(events[events["is_buy"]], ["recent_sell_10b", "freq", "signal_type"], min_events=min_events),
        "sell_by_market_regime": aggregate(events[events["is_sell"]], ["market_regime", "freq", "signal_type"], min_events=min_events),
    }
    for name, table in tables.items():
        table.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
    report = _write_report(output_dir, events, tables, min_events)
    return report


def _write_report(output_dir: Path, events: pd.DataFrame, tables: dict[str, pd.DataFrame], min_events: int) -> Path:
    path = output_dir / "event_analysis_report.md"
    lines = [
        "# V2 事件级小样本分析",
        "",
        "## 样本说明",
        "",
        f"- 事件数量: `{len(events)}`",
        f"- 股票数量: `{events['symbol'].nunique() if not events.empty else 0}`",
        f"- 最小分组事件数: `{min_events}`",
        "- 每一行事件代表一次候选一买、二买、三买、一卖或二卖。",
        "- 分类标签只使用信号发生时及之前的数据；未来收益只作为研究标签。",
        "",
        "## 第一眼结论",
        "",
    ]
    focus = tables["buy_by_market_regime"].copy()
    if focus.empty:
        lines.append("- 当前样本不足，无法形成事件级分层结论。")
    else:
        for _, row in focus.head(8).iterrows():
            lines.append(
                f"- `{row['market_regime']}`环境下 `{row['freq']}{row['signal_type']}`："
                f"事件 {int(row['events'])} 次，未来10根平均收益 {_fmt_pct(row['ret_10b_mean'])}，"
                f"胜率 {_fmt_pct(row['ret_10b_win_rate'])}。"
            )
    lines.extend(["", "## 更大级别过滤", ""])
    higher = tables["buy_by_higher_level"]
    if not higher.empty:
        lines.extend(["```text", higher.head(12).to_string(index=False), "```"])
    lines.extend(["", "## 研究员读法", ""])
    lines.append("- 如果某个信号只在上升/震荡环境有效，它更适合做顺势规则。")
    lines.append("- 如果某个信号在下跌环境仍有效，才可能是左侧逆势规则；否则下跌环境应过滤。")
    lines.append("- 30分钟信号若只有在 higher_level_not_weak=True 时改善，说明它应作为入场细化，而不是独立买点。")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    root = Path(args.report_root)
    cache_dir = Path(args.cache_dir)
    batch_dir = Path(args.batch_dir) if args.batch_dir else _latest_batch(root)
    if not batch_dir.is_absolute():
        batch_dir = root / batch_dir
    output_dir = Path(args.output_dir) if args.output_dir else root / ("event_analysis_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    _, candidates = load_reports(root, batch_dir)
    events = build_event_samples(candidates, cache_dir)
    report = write_outputs(output_dir, events, args.min_events)
    print(f"[done] event report: {report}")


if __name__ == "__main__":
    main()
