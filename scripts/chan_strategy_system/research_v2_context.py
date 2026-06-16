"""Second-layer V2 context research: regime, stock state, multi-timeframe filters."""

from __future__ import annotations

import argparse
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_ROOT = REPO_ROOT / "data" / "reports" / "chan_strategy"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "chan_strategy"
FREQ_DIRS = {"日线": "_", "60分钟": "60_", "30分钟": "30_", "15分钟": "15_"}
HORIZONS = (1, 3, 5, 10, 20)
BUY_SIGNALS = {"一买", "二买", "三买"}
SELL_SIGNALS = {"一卖", "二卖", "三卖"}
FREQ_ORDER = {"日线": 0, "60分钟": 1, "30分钟": 2, "15分钟": 3}
SIGNAL_ORDER = {"一买": 0, "二买": 1, "三买": 2, "一卖": 3, "二卖": 4, "三卖": 5}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build V2 context research report.")
    parser.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--batch-dir", help="Specific v2_batch_* directory. Default: latest.")
    parser.add_argument("--output-dir", help="Default: report-root/research_v2_context_TIMESTAMP")
    parser.add_argument("--min-events", type=int, default=1000)
    parser.add_argument("--save-events", action="store_true", help="Save full event_samples.csv.")
    return parser.parse_args()


def latest_batch(root: Path) -> Path:
    batches = sorted([p for p in root.glob("v2_batch_*") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if not batches:
        raise FileNotFoundError(f"No v2_batch_* directory found under {root}")
    return batches[0]


def resolve_path(root: Path, raw: str | None, default_factory) -> Path:
    if not raw:
        return default_factory()
    path = Path(raw)
    if path.is_absolute():
        return path
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path
    root_path = root / path
    if root_path.exists():
        return root_path
    return root_path


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


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


def label_position(row: pd.Series) -> str:
    if pd.isna(row.get("near_high20")) or pd.isna(row.get("near_low20")):
        return "未知"
    if bool(row.get("near_high20")):
        return "近20日高位"
    if bool(row.get("near_low20")):
        return "近20日低位"
    return "区间中部"


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
    out["ma60"] = out["close"].rolling(60).mean()
    out["above_ma20"] = out["close"] >= out["ma20"]
    out["above_ma60"] = out["close"] >= out["ma60"]
    out["high20"] = out["high"].rolling(20).max()
    out["low20"] = out["low"].rolling(20).min()
    out["near_high20"] = out["close"] >= out["high20"] * 0.98
    out["near_low20"] = out["close"] <= out["low20"] * 1.02
    out["mom20_bucket"] = out["mom20"].map(label_momentum)
    out["mom60_bucket"] = out["mom60"].map(lambda x: label_momentum(x, strong=0.10, weak=-0.06))
    vol_median = out["vol20"].median()
    out["vol20_bucket"] = out["vol20"].map(lambda x: "高波动" if pd.notna(x) and x >= vol_median else ("低波动" if pd.notna(x) else "未知"))
    out["position20"] = out.apply(label_position, axis=1)
    for horizon in HORIZONS:
        out[f"ret_{horizon}b"] = out["close"].shift(-horizon) / out["close"] - 1
        future_low = out["low"].shift(-1).rolling(horizon).min().shift(-(horizon - 1))
        future_high = out["high"].shift(-1).rolling(horizon).max().shift(-(horizon - 1))
        out[f"mfe_{horizon}b"] = future_high / out["close"] - 1
        out[f"mae_{horizon}b"] = future_low / out["close"] - 1
    return out


def build_feature_caches(cache_dir: Path, symbols: list[str]) -> tuple[dict[tuple[str, str], pd.DataFrame], pd.DataFrame]:
    feature_cache: dict[tuple[str, str], pd.DataFrame] = {}
    daily_market_frames = []
    for symbol in symbols:
        for freq in ["日线", "60分钟", "30分钟"]:
            bars = load_bars(cache_dir, symbol, freq)
            if bars.empty:
                continue
            features = prepare_bar_features(bars)
            feature_cache[(symbol, freq)] = features
            if freq == "日线":
                keep = features[["dt", "symbol", "mom20", "above_ma20", "vol20"]].copy()
                keep["date"] = keep["dt"].dt.normalize()
                daily_market_frames.append(keep)
    market = build_market_regime(daily_market_frames)
    return feature_cache, market


def build_market_regime(frames: list[pd.DataFrame]) -> pd.DataFrame:
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
    market["market_regime"] = market.apply(classify_market_regime, axis=1)
    market["market_momentum"] = market["market_ret20"].map(lambda x: label_momentum(x, strong=0.04, weak=-0.02))
    market["market_breadth_bucket"] = market["market_breadth"].map(classify_breadth)
    return market


def classify_market_regime(row: pd.Series) -> str:
    ret20 = row.get("market_ret20")
    breadth = row.get("market_breadth")
    if pd.isna(ret20) or pd.isna(breadth):
        return "未知"
    if ret20 > 0.03 and breadth >= 0.60:
        return "上升"
    if ret20 < -0.02 and breadth <= 0.40:
        return "下跌"
    return "震荡"


def classify_breadth(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "未知"
    if value >= 0.60:
        return "普涨"
    if value <= 0.40:
        return "弱宽度"
    return "分化"


def build_event_samples(candidates: pd.DataFrame, cache_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame()
    candidates = candidates.copy()
    candidates["dt"] = pd.to_datetime(candidates["dt"])
    symbols = sorted(candidates["symbol"].astype(str).unique().tolist())
    feature_cache, market = build_feature_caches(cache_dir, symbols)

    event_frames = []
    feature_cols = [
        "dt",
        "bar_index",
        "close",
        "mom20",
        "mom60",
        "mom20_bucket",
        "mom60_bucket",
        "vol20_bucket",
        "above_ma20",
        "above_ma60",
        "position20",
        *[f"ret_{h}b" for h in HORIZONS],
        *[f"mfe_{h}b" for h in HORIZONS],
        *[f"mae_{h}b" for h in HORIZONS],
    ]
    for (symbol, freq), group in candidates.groupby(["symbol", "freq"], sort=False):
        features = feature_cache.get((symbol, freq))
        if features is None or features.empty:
            continue
        cols = [c for c in feature_cols if c in features.columns]
        merged = group.merge(features[cols], on="dt", how="left", suffixes=("", "_feature"))
        event_frames.append(merged)
    if not event_frames:
        return pd.DataFrame(), market

    events = pd.concat(event_frames, ignore_index=True)
    events["date"] = pd.to_datetime(events["dt"]).dt.normalize()
    events["is_buy"] = events["signal_type"].isin(BUY_SIGNALS)
    events["is_sell"] = events["signal_type"].isin(SELL_SIGNALS)
    events = add_market_labels(events, market)
    events = add_higher_level_labels(events, feature_cache)
    events["stock_state"] = events.apply(classify_stock_state, axis=1)
    return events, market


def add_market_labels(events: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    if market.empty:
        events["market_regime"] = "未知"
        events["market_momentum"] = "未知"
        events["market_breadth_bucket"] = "未知"
        return events
    events_sorted = events.sort_values("date").reset_index(drop=True)
    market_sorted = market.sort_values("date").reset_index(drop=True)
    return pd.merge_asof(events_sorted, market_sorted, on="date", direction="backward")


def asof_context(events: pd.DataFrame, features: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = events.copy()
    if features.empty:
        out[f"{prefix}_mom20_bucket"] = "未知"
        out[f"{prefix}_above_ma20"] = pd.NA
        out[f"{prefix}_mom20"] = pd.NA
        return out
    left = out.sort_values("dt")
    right = features[["dt", "mom20", "mom20_bucket", "above_ma20"]].sort_values("dt")
    merged = pd.merge_asof(left, right, on="dt", direction="backward", suffixes=("", f"_{prefix}"))
    merged = merged.rename(
        columns={
            "mom20_bucket_" + prefix: f"{prefix}_mom20_bucket",
            "above_ma20_" + prefix: f"{prefix}_above_ma20",
            "mom20_" + prefix: f"{prefix}_mom20",
        }
    )
    return merged.sort_index()


def add_higher_level_labels(events: pd.DataFrame, feature_cache: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for symbol, group in events.groupby("symbol", sort=False):
        g = group.copy()
        daily = feature_cache.get((symbol, "日线"), pd.DataFrame())
        hour = feature_cache.get((symbol, "60分钟"), pd.DataFrame())

        if daily.empty:
            g["daily_mom20_bucket"] = "未知"
            g["daily_above_ma20"] = pd.NA
            g["daily_mom20"] = pd.NA
        else:
            left = g.sort_values("dt")
            right = daily[["dt", "mom20", "mom20_bucket", "above_ma20"]].sort_values("dt")
            g = pd.merge_asof(left, right, on="dt", direction="backward", suffixes=("", "_daily"))
            g = g.rename(
                columns={
                    "mom20_bucket_daily": "daily_mom20_bucket",
                    "above_ma20_daily": "daily_above_ma20",
                    "mom20_daily": "daily_mom20",
                }
            )

        if hour.empty:
            g["hour_mom20_bucket"] = "未知"
            g["hour_above_ma20"] = pd.NA
            g["hour_mom20"] = pd.NA
        else:
            left = g.sort_values("dt")
            right = hour[["dt", "mom20", "mom20_bucket", "above_ma20"]].sort_values("dt")
            g = pd.merge_asof(left, right, on="dt", direction="backward", suffixes=("", "_hour"))
            g = g.rename(
                columns={
                    "mom20_bucket_hour": "hour_mom20_bucket",
                    "above_ma20_hour": "hour_above_ma20",
                    "mom20_hour": "hour_mom20",
                }
            )

        frames.append(g)
    out = pd.concat(frames, ignore_index=True)
    out["daily_not_weak"] = out["daily_mom20_bucket"].fillna("未知") != "弱"
    out["hour_not_weak"] = out["hour_mom20_bucket"].fillna("未知") != "弱"
    out["higher_level_not_weak"] = True
    out.loc[out["freq"] == "日线", "higher_level_not_weak"] = out.loc[out["freq"] == "日线", "market_regime"].ne("下跌")
    out.loc[out["freq"] == "60分钟", "higher_level_not_weak"] = out.loc[out["freq"] == "60分钟", "daily_not_weak"]
    mask_30 = out["freq"] == "30分钟"
    out.loc[mask_30, "higher_level_not_weak"] = out.loc[mask_30, "daily_not_weak"] & out.loc[mask_30, "hour_not_weak"]
    return out


def classify_stock_state(row: pd.Series) -> str:
    mom = row.get("mom20_bucket", "未知")
    above = row.get("above_ma20")
    if mom == "强" and bool(above):
        return "强势"
    if mom == "弱" and not bool(above):
        return "弱势"
    return "中性"


def aggregate(events: pd.DataFrame, group_cols: list[str], min_events: int = 0) -> pd.DataFrame:
    rows = []
    if events.empty:
        return pd.DataFrame()
    for keys, group in events.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        row["events"] = len(group)
        row["symbols"] = group["symbol"].nunique()
        for horizon in [5, 10, 20]:
            ret_col = f"ret_{horizon}b"
            valid = pd.to_numeric(group[ret_col], errors="coerce").dropna()
            row[f"ret_{horizon}b_mean"] = float(valid.mean()) if not valid.empty else math.nan
            row[f"ret_{horizon}b_median"] = float(valid.median()) if not valid.empty else math.nan
            row[f"ret_{horizon}b_win_rate"] = float((valid > 0).mean()) if not valid.empty else math.nan
            mae_col = f"mae_{horizon}b"
            if mae_col in group.columns:
                mae = pd.to_numeric(group[mae_col], errors="coerce").dropna()
                row[f"mae_{horizon}b_mean"] = float(mae.mean()) if not mae.empty else math.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    if min_events and not out.empty:
        out = out[out["events"] >= min_events]
    if "ret_10b_mean" in out.columns:
        out = out.sort_values(["ret_10b_mean", "ret_10b_win_rate"], ascending=False)
    return out.reset_index(drop=True)


def sort_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    keys = []
    for _, row in out.iterrows():
        keys.append(
            (
                FREQ_ORDER.get(row.get("freq"), 99),
                SIGNAL_ORDER.get(row.get("signal_type"), 99),
                str(row.get("market_regime", "")),
                str(row.get("stock_state", "")),
            )
        )
    out["_sort"] = keys
    return out.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)


def pct(value: float | int | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:.{digits}f}%"


def fmt_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col.endswith("_mean") or col.endswith("_median") or col.endswith("_win_rate"):
            out[col] = out[col].map(pct)
    return out


def md_table(df: pd.DataFrame, columns: list[str], rename: dict[str, str] | None = None, max_rows: int | None = None) -> str:
    if df.empty:
        return "暂无数据。"
    use = df[columns].copy()
    if rename:
        use = use.rename(columns=rename)
    if max_rows:
        use = use.head(max_rows)
    return use.to_markdown(index=False)


def write_report(output_dir: Path, batch_dir: Path, events: pd.DataFrame, market: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "context_research_report.md"
    regime_counts = Counter(events["market_regime"].fillna("未知")) if not events.empty else Counter()
    signal_counts = Counter(events["signal_type"].fillna("未知")) if not events.empty else Counter()
    freq_counts = Counter(events["freq"].fillna("未知")) if not events.empty else Counter()

    market_buy = fmt_table(tables["buy_by_market_regime"])
    state_buy = fmt_table(tables["buy_by_stock_state"])
    multi_buy = fmt_table(tables["buy_by_multilevel"])
    matrix_buy = fmt_table(tables["buy_context_matrix"])
    sell_regime = fmt_table(tables["sell_by_market_regime"])

    lines = [
        "# 沪深300缠论信号第二层研究报告",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 研究定位",
        "",
        "本报告承接第一层裸信号研究，重点转向三个上下文变量：市场环境、个股状态、多周期联立。",
        "",
        f"- 批量目录：`{batch_dir}`",
        f"- 事件数量：`{len(events)}`",
        f"- 覆盖股票：`{events['symbol'].nunique() if not events.empty else 0}`",
        f"- 周期分布：`{dict(freq_counts)}`",
        f"- 信号分布：`{dict(signal_counts)}`",
        f"- 市场环境分布：`{dict(regime_counts)}`",
        "",
        "市场环境采用样本池等权口径：20日平均收益和站上 MA20 的股票比例共同划分为上升、震荡、下跌。该分类是研究用粗标签，不等同于最终交易模型。",
        "",
        "## 2. 市场环境：买点信号在哪种市场更有效",
        "",
        md_table(
            market_buy,
            ["market_regime", "freq", "signal_type", "events", "symbols", "ret_10b_mean", "ret_10b_win_rate", "ret_20b_mean", "ret_20b_win_rate"],
            {
                "market_regime": "市场环境",
                "freq": "周期",
                "signal_type": "信号",
                "events": "事件数",
                "symbols": "股票数",
                "ret_10b_mean": "10根均值",
                "ret_10b_win_rate": "10根胜率",
                "ret_20b_mean": "20根均值",
                "ret_20b_win_rate": "20根胜率",
            },
            max_rows=15,
        ),
        "",
        "研究解读：如果某个买点只在上升或震荡环境有效，它就不应被设计成全市场通用开仓规则；如果下跌环境中也稳定有效，才可能是左侧修复类规则。",
        "",
        "## 3. 个股状态：强势、弱势、中性下的信号差异",
        "",
        md_table(
            state_buy,
            ["stock_state", "freq", "signal_type", "events", "symbols", "ret_10b_mean", "ret_10b_win_rate", "ret_20b_mean", "ret_20b_win_rate"],
            {
                "stock_state": "个股状态",
                "freq": "周期",
                "signal_type": "信号",
                "events": "事件数",
                "symbols": "股票数",
                "ret_10b_mean": "10根均值",
                "ret_10b_win_rate": "10根胜率",
                "ret_20b_mean": "20根均值",
                "ret_20b_win_rate": "20根胜率",
            },
            max_rows=15,
        ),
        "",
        "研究解读：如果强势状态下的三买/二买明显优于弱势状态，说明缠论买点更适合做顺势确认；如果弱势状态下的一买有效，才说明它具备左侧反转价值。",
        "",
        "## 4. 多周期联立：高一级别不弱是否改善信号",
        "",
        md_table(
            multi_buy,
            ["higher_level_not_weak", "freq", "signal_type", "events", "symbols", "ret_10b_mean", "ret_10b_win_rate", "ret_20b_mean", "ret_20b_win_rate"],
            {
                "higher_level_not_weak": "高周期不弱",
                "freq": "周期",
                "signal_type": "信号",
                "events": "事件数",
                "symbols": "股票数",
                "ret_10b_mean": "10根均值",
                "ret_10b_win_rate": "10根胜率",
                "ret_20b_mean": "20根均值",
                "ret_20b_win_rate": "20根胜率",
            },
            max_rows=18,
        ),
        "",
        "研究解读：这是 V3 策略最关键的证据之一。如果高周期不弱能提升小周期买点表现，多级别联立就有统计基础；反之，多级别可能只是主观叙事。",
        "",
        "## 5. 交叉上下文：市场环境 + 个股状态 + 多周期",
        "",
        md_table(
            matrix_buy,
            [
                "market_regime",
                "stock_state",
                "higher_level_not_weak",
                "freq",
                "signal_type",
                "events",
                "symbols",
                "ret_10b_mean",
                "ret_10b_win_rate",
                "ret_20b_mean",
                "ret_20b_win_rate",
            ],
            {
                "market_regime": "市场",
                "stock_state": "个股",
                "higher_level_not_weak": "高周期不弱",
                "freq": "周期",
                "signal_type": "信号",
                "events": "事件数",
                "symbols": "股票数",
                "ret_10b_mean": "10根均值",
                "ret_10b_win_rate": "10根胜率",
                "ret_20b_mean": "20根均值",
                "ret_20b_win_rate": "20根胜率",
            },
            max_rows=20,
        ),
        "",
        "研究解读：这张表用于寻找 V3 候选规则。优先考虑事件数足够、覆盖股票广、10根和20根表现一致、胜率和平均收益同时改善的组合。",
        "",
        "## 6. 卖点在不同市场环境下的含义",
        "",
        md_table(
            sell_regime,
            ["market_regime", "freq", "signal_type", "events", "symbols", "ret_10b_mean", "ret_10b_win_rate", "ret_20b_mean", "ret_20b_win_rate"],
            {
                "market_regime": "市场环境",
                "freq": "周期",
                "signal_type": "信号",
                "events": "事件数",
                "symbols": "股票数",
                "ret_10b_mean": "10根均值",
                "ret_10b_win_rate": "10根胜率",
                "ret_20b_mean": "20根均值",
                "ret_20b_win_rate": "20根胜率",
            },
            max_rows=15,
        ),
        "",
        "研究解读：卖点后仍上涨，说明它不是简单清仓信号；卖点后显著走弱，才适合进入减仓或风控规则。",
        "",
        "## 7. 第二层研究结论模板",
        "",
        "阅读本报告时，建议按以下顺序判断：",
        "",
        "1. 先看市场环境，排除明显失效环境；",
        "2. 再看个股状态，确认是顺势信号还是左侧修复信号；",
        "3. 最后看多周期联立，判断高周期过滤是否真的改善结果；",
        "4. 只有同时满足样本数、覆盖股票数、收益、胜率、20根延续性都较好的组合，才进入 V3 候选规则。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run(args: argparse.Namespace) -> Path:
    report_root = Path(args.report_root)
    cache_dir = Path(args.cache_dir)
    batch_dir = resolve_path(report_root, args.batch_dir, lambda: latest_batch(report_root))
    output_dir = Path(args.output_dir) if args.output_dir else report_root / ("research_v2_context_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir

    candidates = read_csv(batch_dir / "batch_candidates.csv")
    if candidates.empty:
        raise FileNotFoundError(f"Missing batch_candidates.csv in {batch_dir}")

    events, market = build_event_samples(candidates, cache_dir)
    if events.empty:
        raise RuntimeError("No event samples generated; check bar cache.")

    output_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "buy_by_market_regime": aggregate(events[events["is_buy"]], ["market_regime", "freq", "signal_type"], args.min_events),
        "buy_by_stock_state": aggregate(events[events["is_buy"]], ["stock_state", "freq", "signal_type"], args.min_events),
        "buy_by_multilevel": aggregate(events[events["is_buy"]], ["higher_level_not_weak", "freq", "signal_type"], args.min_events),
        "buy_context_matrix": aggregate(
            events[events["is_buy"]],
            ["market_regime", "stock_state", "higher_level_not_weak", "freq", "signal_type"],
            args.min_events,
        ),
        "sell_by_market_regime": aggregate(events[events["is_sell"]], ["market_regime", "freq", "signal_type"], args.min_events),
    }
    for name, table in tables.items():
        table.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
    market.to_csv(output_dir / "market_regime.csv", index=False, encoding="utf-8-sig")
    if args.save_events:
        events.to_csv(output_dir / "event_samples.csv", index=False, encoding="utf-8-sig")
    else:
        keep_cols = [
            "symbol",
            "freq",
            "dt",
            "close",
            "signal_type",
            "market_regime",
            "stock_state",
            "higher_level_not_weak",
            "ret_5b",
            "ret_10b",
            "ret_20b",
        ]
        events[keep_cols].to_csv(output_dir / "event_samples_light.csv", index=False, encoding="utf-8-sig")
    report = write_report(output_dir, batch_dir, events, market, tables)
    print(f"[done] context research report: {report}")
    return report


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
