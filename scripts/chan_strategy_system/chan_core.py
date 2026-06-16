"""V2 Chan strategy core: multi-level structure and signal research base.

This module builds a research-oriented Chan analysis report. It deliberately
separates structure / signal diagnosis from trading execution so later versions
can test hypotheses without turning the strategy into an opaque black box.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "chan_strategy"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "reports" / "chan_strategy"
DEFAULT_ANALYSIS_FREQS = "日线,60分钟,30分钟"
HORIZONS = (1, 3, 5, 10, 20)
SIGNAL_CACHE_VERSION = "v2_signals_20260613a"
QUICK_MAX_BARS_BY_FREQ = {
    "日线": 900,
    "60分钟": 2200,
    "30分钟": 2600,
    "15分钟": 3200,
}


def _avoid_unbuilt_source_package() -> None:
    native_dir = REPO_ROOT / "czsc" / "_native"
    local_native_built = any(native_dir.glob("*.pyd")) or any(native_dir.glob("*.so"))
    if local_native_built:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        return

    root_variants = {str(REPO_ROOT), str(REPO_ROOT.resolve()), ""}
    sys.path[:] = [
        path
        for path in sys.path
        if path not in root_variants and Path(path or ".").resolve() != REPO_ROOT.resolve()
    ]
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))


_avoid_unbuilt_source_package()


@dataclass
class CoreConfig:
    mode: str = "mock"
    symbols: str = "002202.SZ"
    start_date: str = "20200101"
    end_date: str = "20240601"
    backtest_start: str = "20200701"
    analysis_freqs: str = DEFAULT_ANALYSIS_FREQS
    speed_mode: str = "standard"
    fq: str = "后复权"
    cache_dir: str = str(DEFAULT_CACHE_DIR)
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    seed: int = 42


def parse_args() -> CoreConfig:
    parser = argparse.ArgumentParser(description="Run V2 Chan structure and signal research core.")
    parser.add_argument("--mode", choices=["mock", "tushare"], default=None)
    parser.add_argument("--symbols")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--backtest-start")
    parser.add_argument("--analysis-freqs")
    parser.add_argument("--speed-mode", choices=["quick", "standard"], default=None)
    parser.add_argument("--fq")
    parser.add_argument("--cache-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    merged = asdict(CoreConfig())
    for key, value in vars(args).items():
        if value is not None:
            merged[key] = value
    return CoreConfig(**merged)


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value)


def _asset_symbol(symbol: str) -> str:
    return symbol.split("#", 1)[0].strip().upper()


def _infer_asset(symbol: str) -> str:
    from tushare_client import infer_asset

    return infer_asset(_asset_symbol(symbol))


def _normalize_symbols(symbols: str) -> list[str]:
    values = []
    seen = set()
    for raw in symbols.split(","):
        symbol = raw.strip().upper()
        if not symbol:
            continue
        if "#" in symbol:
            symbol = symbol.split("#", 1)[0]
        if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", symbol):
            raise ValueError(f"股票代码格式错误：{raw}; 应为 002202.SZ 这样的格式")
        if symbol not in seen:
            values.append(symbol)
            seen.add(symbol)
    if not values:
        raise ValueError("至少需要一个股票代码")
    return values


def _analysis_freqs(cfg: CoreConfig) -> list[str]:
    freqs = [x.strip() for x in cfg.analysis_freqs.split(",") if x.strip()]
    if not freqs:
        raise ValueError("analysis_freqs 不能为空")
    return freqs


def _validate_speed_mode(cfg: CoreConfig) -> str:
    mode = (cfg.speed_mode or "standard").strip().lower()
    if mode not in {"quick", "standard"}:
        raise ValueError("speed_mode 只能是 quick 或 standard")
    return mode


def _require_tushare_token() -> None:
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not configured. Please set it locally before real-data runs.")
    print(f"TUSHARE_TOKEN configured: True; length={len(token)}")


def normalize_kline_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"])
    df["symbol"] = df["symbol"].astype(str)
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    return df[["symbol", "dt", "open", "high", "low", "close", "vol", "amount"]].dropna().reset_index(drop=True)


def _load_mock_bars(symbol: str, freq: str, cfg: CoreConfig) -> tuple[list, pd.DataFrame]:
    from czsc import format_standard_kline
    from czsc.mock import generate_symbol_kines

    df = generate_symbol_kines(symbol, freq, cfg.start_date, cfg.end_date, seed=cfg.seed)
    df = normalize_kline_dtypes(df)
    return format_standard_kline(df, freq=freq), df


def _load_tushare_bars(symbol: str, freq: str, cfg: CoreConfig) -> tuple[list, pd.DataFrame]:
    from czsc import format_standard_kline
    from tushare_client import fetch_pro_bar_daily, fetch_pro_bar_minutes, init_tushare

    asset = _infer_asset(symbol)
    cache_dir = Path(cfg.cache_dir) / "bars" / _safe_name(freq)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{symbol}_{asset}_{cfg.start_date}_{cfg.end_date}_{_safe_name(cfg.fq)}.parquet"
    if cache_file.exists():
        print(f"[v2-cache-hit] {symbol} {freq} {cache_file}", flush=True)
        df = pd.read_parquet(cache_file)
    else:
        print(f"[v2-fetch] {symbol} {freq} asset={asset} range={cfg.start_date}-{cfg.end_date}", flush=True)
        adj = "qfq" if cfg.fq == "前复权" else "hfq"
        pro = init_tushare()
        if freq == "日线":
            df = fetch_pro_bar_daily(pro, symbol, sdt=cfg.start_date, edt=cfg.end_date, asset=asset, adj=adj)
        else:
            minute_freq = freq.replace("分钟", "min")
            df = fetch_pro_bar_minutes(pro, symbol, sdt=cfg.start_date, edt=cfg.end_date, freq=minute_freq, asset=asset, adj=adj)
        if df.empty:
            raise RuntimeError(f"No bars returned for {symbol} {freq}.")
        df.to_parquet(cache_file, index=False)
        print(f"[v2-cache-write] {symbol} {freq} rows={len(df)} {cache_file}", flush=True)
    df = normalize_kline_dtypes(df)
    print(f"[v2-format] {symbol} {freq} rows={len(df)}", flush=True)
    bars = format_standard_kline(df, freq=freq)
    print(f"[v2-format-done] {symbol} {freq} bars={len(bars)}", flush=True)
    return bars, df


def load_bars(symbol: str, freq: str, cfg: CoreConfig) -> tuple[list, pd.DataFrame]:
    if cfg.mode == "mock":
        return _load_mock_bars(symbol, freq, cfg)
    return _load_tushare_bars(symbol, freq, cfg)


def apply_speed_mode(symbol: str, freq: str, bars: list, df: pd.DataFrame, cfg: CoreConfig) -> tuple[list, pd.DataFrame]:
    """Trim old bars in quick mode for interactive single-symbol diagnosis."""
    if _validate_speed_mode(cfg) != "quick":
        return bars, df
    max_bars = QUICK_MAX_BARS_BY_FREQ.get(freq)
    if not max_bars or len(bars) <= max_bars:
        return bars, df
    bars = bars[-max_bars:]
    df = df.tail(max_bars).reset_index(drop=True)
    first_dt = pd.to_datetime(df["dt"].iloc[0]) if not df.empty else "-"
    last_dt = pd.to_datetime(df["dt"].iloc[-1]) if not df.empty else "-"
    print(
        f"[v2-quick-trim] {symbol} {freq} bars={len(bars)} window={first_dt}~{last_dt}",
        flush=True,
    )
    return bars, df


def signal_configs(freq: str) -> list[dict]:
    candidates = [
        ("cxt_bi_status_V230101", {"freq": freq}),
        ("cxt_first_buy_V221126", {"freq": freq, "di": 1}),
        ("cxt_first_sell_V221126", {"freq": freq, "di": 1}),
        ("cxt_second_bs_V240524", {"freq": freq, "di": 1, "w": 9, "t": 2}),
        ("cxt_third_buy_V230228", {"freq": freq, "di": 1}),
        ("cxt_bi_trend_V230913", {"freq": freq, "di": 1, "n": 6}),
        ("tas_ma_base_V221101", {"freq": freq, "di": 1, "ma_type": "SMA", "timeperiod": 5}),
        ("tas_ma_base_V221101", {"freq": freq, "di": 1, "ma_type": "SMA", "timeperiod": 20}),
    ]
    configs = []
    for name, params in candidates:
        resolved = resolve_signal_name(name)
        if resolved is None:
            print(f"[v2-warn] signal not available in current czsc: {name}")
            continue
        configs.append({"name": resolved, **params})
    return configs


def resolve_signal_name(name: str):
    """Resolve signal names across old Python CZSC and new Rust registry CZSC.

    Old PyPI ``czsc==0.10.x`` imports signal functions from
    ``czsc.signals.{cxt,tas}`` and accepts callables in ``signals_config``.
    Newer Rust-backed CZSC accepts short registry names. Try the old callable
    first; if the old signal namespace does not exist, keep the short name.
    """
    module_key = name.split("_", 1)[0]
    if module_key not in {"cxt", "tas", "bar", "vol", "pressure", "obv", "cvolp"}:
        return name
    try:
        module = importlib.import_module(f"czsc.signals.{module_key}")
    except ModuleNotFoundError:
        return name
    return getattr(module, name, None)


def _signals_cache_path(symbol: str, freq: str, bars: list, cfg: CoreConfig) -> Path:
    first_dt = _safe_name(str(getattr(bars[0], "dt", "na"))) if bars else "na"
    last_dt = _safe_name(str(getattr(bars[-1], "dt", "na"))) if bars else "na"
    cache_dir = Path(cfg.cache_dir) / "signals" / _safe_name(freq)
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = "_".join(
        [
            _safe_name(symbol),
            _safe_name(cfg.fq),
            _safe_name(cfg.backtest_start),
            _safe_name(_validate_speed_mode(cfg)),
            str(len(bars)),
            first_dt,
            last_dt,
            SIGNAL_CACHE_VERSION,
        ]
    )
    return cache_dir / f"{name}.parquet"


def generate_signals(symbol: str, bars: list, freq: str, cfg: CoreConfig) -> pd.DataFrame:
    from czsc import generate_czsc_signals

    if len(bars) < 100:
        return pd.DataFrame()
    cache_file = _signals_cache_path(symbol, freq, bars, cfg)
    if cache_file.exists():
        try:
            print(f"[v2-signals-cache-hit] {symbol} {freq} {cache_file}", flush=True)
            return pd.read_parquet(cache_file)
        except Exception as exc:  # noqa: BLE001
            print(f"[v2-warn] failed to read signal cache; regenerating: {type(exc).__name__}: {exc}", flush=True)
    configs = signal_configs(freq)
    if not configs:
        print(f"[v2-warn] no compatible signals available for {freq}")
        return pd.DataFrame()
    print(f"[v2-signals] {freq} bars={len(bars)} signals={len(configs)}", flush=True)
    df = generate_czsc_signals(bars, signals_config=configs, df=True, sdt=cfg.backtest_start)
    if df is None:
        return pd.DataFrame()
    df = pd.DataFrame(df)
    try:
        df.to_parquet(cache_file, index=False)
        print(f"[v2-signals-cache-write] {symbol} {freq} rows={len(df)} {cache_file}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[v2-warn] failed to write signal cache: {type(exc).__name__}: {exc}", flush=True)
    print(f"[v2-signals-done] {freq} rows={len(df)} cols={len(df.columns)}", flush=True)
    return df


def signal_columns(df: pd.DataFrame) -> list[str]:
    return [col for col in df.columns if isinstance(col, str) and len(col.split("_")) == 3]


def _split_signal_value(value: object) -> tuple[str, str, str, str]:
    parts = str(value).split("_")
    parts = (parts + ["任意", "任意", "0"])[:4]
    return parts[0], parts[1], parts[2], parts[3]


def classify_candidate(key: str, value: object) -> str | None:
    v1, _, _, _ = _split_signal_value(value)
    text = f"{key}_{value}"
    if "其他" in str(value):
        return None
    if "BUY1" in key or v1 == "一买":
        return "一买"
    if "SELL1" in key or v1 == "一卖":
        return "一卖"
    if "第二买卖点" in key and v1 in {"二买", "二卖"}:
        return v1
    if "三买辅助" in key and v1 == "三买":
        return "三买"
    if "BS3辅助" in key and v1 in {"三买", "三卖"}:
        return v1
    if "表里关系" in key and v1 in {"向上", "向下"}:
        return None
    if any(token in text for token in ["一买", "二买", "三买", "一卖", "二卖", "三卖"]):
        return v1 if v1 in {"一买", "二买", "三买", "一卖", "二卖", "三卖"} else None
    return None


def extract_candidates(symbol: str, freq: str, sigs: pd.DataFrame) -> pd.DataFrame:
    if sigs.empty:
        return pd.DataFrame(columns=["symbol", "freq", "dt", "close", "signal_type", "signal_key", "signal_value"])
    rows = []
    for _, row in sigs.iterrows():
        for col in signal_columns(sigs):
            sig_type = classify_candidate(col, row.get(col))
            if not sig_type:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "freq": freq,
                    "dt": pd.to_datetime(row.get("dt")),
                    "close": float(row.get("close")),
                    "signal_type": sig_type,
                    "signal_key": col,
                    "signal_value": row.get(col),
                }
            )
    return pd.DataFrame(rows)


def signal_stats(symbol: str, freq: str, sigs: pd.DataFrame) -> pd.DataFrame:
    if sigs.empty:
        return pd.DataFrame()
    df = sigs.copy()
    df["dt"] = pd.to_datetime(df["dt"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    rows = []
    for col in signal_columns(df):
        labels = df[col].map(lambda x: classify_candidate(col, x))
        mask = labels.notna()
        if not mask.any():
            continue
        for sig_type, idx in labels[mask].groupby(labels[mask]).groups.items():
            subset = df.loc[list(idx)].copy()
            row = {"symbol": symbol, "freq": freq, "signal_type": sig_type, "signal_key": col, "count": len(subset)}
            for horizon in HORIZONS:
                future = df["close"].shift(-horizon)
                ret = future.loc[subset.index] / subset["close"] - 1
                ret = ret.dropna()
                row[f"ret_{horizon}b_mean"] = round(float(ret.mean()), 6) if not ret.empty else None
                row[f"ret_{horizon}b_win_rate"] = round(float((ret > 0).mean()), 4) if not ret.empty else None
                row[f"ret_{horizon}b_sample"] = int(ret.count())
            rows.append(row)
    return pd.DataFrame(rows)


def _to_number(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        if pd.isna(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _fmt_pct(value) -> str:
    number = _to_number(value)
    return "-" if number is None else f"{number:.2%}"


def _fmt_price(value) -> str:
    number = _to_number(value)
    return "-" if number is None else f"{number:.2f}"


def _signal_name(freq: str, sig_type: str) -> str:
    return f"{freq}{sig_type}"


def build_readable_analysis(states: pd.DataFrame, candidates: pd.DataFrame, stats: pd.DataFrame, summary: dict) -> dict:
    """Build a plain-language layer for non-quant readers."""
    analysis = {
        "one_liner": _one_liner(states, candidates, stats, summary),
        "structure": _structure_notes(states),
        "recent_signals": _recent_signal_notes(candidates),
        "signal_stats": _signal_stat_notes(stats),
        "risk": _risk_notes(states, candidates, stats),
        "next_steps": _next_step_notes(summary),
    }
    return analysis


def _one_liner(states: pd.DataFrame, candidates: pd.DataFrame, stats: pd.DataFrame, summary: dict) -> str:
    if states.empty:
        return "本次没有生成有效结构状态，优先检查数据和信号函数是否可用。"
    symbol = str(states.iloc[0].get("symbol", "当前标的"))
    rating = summary.get("rating", "-")
    score = summary.get("score", "-")
    big = states[states["freq"].isin(["日线", "60分钟"])]
    strong = int((big["trend"] == "偏强").sum()) if not big.empty else 0
    weak = int((states["trend"] == "偏弱").sum())
    recent_buy = _latest_candidate(candidates, {"一买", "二买", "三买"})
    recent_sell = _latest_candidate(candidates, {"一卖", "二卖", "三卖"})
    parts = [f"{symbol} 当前综合评分 {score}，评级为{rating}。"]
    if strong:
        parts.append(f"大级别有 {strong} 个周期偏强。")
    if weak:
        parts.append(f"同时有 {weak} 个周期偏弱，需要防止短线回撤。")
    if recent_buy:
        parts.append(f"最近买点候选是{recent_buy['freq']}{recent_buy['signal_type']}。")
    if recent_sell:
        parts.append(f"最近也出现过{recent_sell['freq']}{recent_sell['signal_type']}，说明信号并非单边一致。")
    return "".join(parts)


def _structure_notes(states: pd.DataFrame) -> list[str]:
    if states.empty:
        return ["没有多级别结构状态。"]
    notes = []
    for row in states.to_dict("records"):
        freq = row.get("freq", "-")
        trend = row.get("trend", "-")
        close = _fmt_price(row.get("close"))
        risk = _fmt_price(row.get("risk_price"))
        bi_status = str(row.get("bi_status", "-")).split("_")[0]
        ma5 = str(row.get("ma5_state", "-")).split("_")[0]
        ma20 = str(row.get("ma20_state", "-")).split("_")[0]
        text = f"{freq}：收盘 {close}，结构{trend}，笔状态{bi_status}，短均线{ma5}，中期均线{ma20}，短线风险参考位约 {risk}。"
        notes.append(text)
    return notes


def _recent_signal_notes(candidates: pd.DataFrame) -> list[str]:
    if candidates.empty:
        return ["最近没有识别到一买、二买、三买或卖点候选。"]
    df = candidates.copy()
    df["dt"] = pd.to_datetime(df["dt"])
    recent = df.sort_values("dt").tail(30)
    counts = recent.groupby(["freq", "signal_type"]).size().sort_values(ascending=False)
    notes = []
    for (freq, sig_type), count in counts.head(4).items():
        last = recent[(recent["freq"] == freq) & (recent["signal_type"] == sig_type)].sort_values("dt").tail(1).iloc[0]
        notes.append(f"最近 30 条候选中，{freq}{sig_type} 出现 {count} 次；最近一次在 {last['dt']}，价格约 {_fmt_price(last.get('close'))}。")
    latest_buy = _latest_candidate(df, {"一买", "二买", "三买"})
    latest_sell = _latest_candidate(df, {"一卖", "二卖", "三卖"})
    if latest_buy and latest_sell:
        notes.append(f"买点与卖点都出现过，说明这里更适合观察结构确认，不适合只凭单个信号直接下结论。")
    return notes


def _signal_stat_notes(stats: pd.DataFrame) -> list[str]:
    if stats.empty:
        return ["没有足够信号样本生成统计结论。"]
    df = stats.copy()
    for col in df.columns:
        if col.startswith("ret_") or col == "count":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    notes = []
    notes.append(
        "这部分统计的是候选信号出现后，未来若干根K线的平均收益和上涨比例；它是研究标签，不是已经可以直接执行的买卖规则。"
    )
    notes.append(
        "阅读顺序建议先看样本数 count，再看未来10根平均收益，最后看胜率；样本少于10次的信号只能当线索，暂时不要当结论。"
    )
    coverage = _signal_coverage_notes(df)
    if coverage:
        notes.extend(coverage)
    buy = df[df["signal_type"].isin(["一买", "二买", "三买"])].copy()
    if not buy.empty and "ret_10b_mean" in buy.columns:
        ranked = buy[buy["count"].fillna(0) >= 10].sort_values("ret_10b_mean", ascending=False)
        if not ranked.empty:
            best = ranked.iloc[0]
            notes.append(
                f"买点里当前表现最好的是{_signal_name(best['freq'], best['signal_type'])}："
                f"样本 {int(best['count'])} 次，未来10根平均收益 {_fmt_pct(best.get('ret_10b_mean'))}，"
                f"胜率 {_fmt_pct(best.get('ret_10b_win_rate'))}。"
            )
        for _, row in ranked.head(3).iterrows():
            notes.append(
                f"{_signal_name(row['freq'], row['signal_type'])}：样本 {int(row['count'])} 次，"
                f"未来 10 根平均收益 {_fmt_pct(row.get('ret_10b_mean'))}，胜率 {_fmt_pct(row.get('ret_10b_win_rate'))}。"
            )
        weak_buy = ranked[ranked["ret_10b_mean"].fillna(0) <= 0]
        if not weak_buy.empty:
            row = weak_buy.iloc[0]
            notes.append(
                f"需要警惕：{_signal_name(row['freq'], row['signal_type'])}虽然出现次数不少，"
                f"但未来10根平均收益不占优，暂时不适合作为独立买入规则。"
            )
    sell = df[df["signal_type"].isin(["一卖", "二卖", "三卖"])].copy()
    if not sell.empty and "ret_10b_mean" in sell.columns:
        ranked = sell[sell["count"].fillna(0) >= 10].sort_values("ret_10b_mean")
        if not ranked.empty:
            best_risk = ranked.iloc[0]
            notes.append(
                f"卖点/风险提示里，{_signal_name(best_risk['freq'], best_risk['signal_type'])}后续10根平均收益最低，"
                f"为 {_fmt_pct(best_risk.get('ret_10b_mean'))}，可优先作为风险过滤线索。"
            )
        for _, row in ranked.head(2).iterrows():
            notes.append(
                f"{_signal_name(row['freq'], row['signal_type'])}：样本 {int(row['count'])} 次，"
                f"未来 10 根平均收益 {_fmt_pct(row.get('ret_10b_mean'))}，可作为风险提示观察。"
            )
    if not notes:
        notes.append("候选信号样本偏少，暂时不适合据此制定交易规则。")
    return notes


def _signal_coverage_notes(df: pd.DataFrame) -> list[str]:
    notes = []
    for family, types in [("买点", ["一买", "二买", "三买"]), ("卖点", ["一卖", "二卖", "三卖"])]:
        subset = df[df["signal_type"].isin(types)].copy()
        if subset.empty:
            notes.append(f"本次没有识别到可统计的{family}信号。")
            continue
        parts = []
        for sig_type in types:
            sig = subset[subset["signal_type"] == sig_type]
            total = int(sig["count"].sum()) if not sig.empty else 0
            if total:
                parts.append(f"{sig_type} {total} 次")
            else:
                parts.append(f"{sig_type} 0 次")
        notes.append(f"{family}覆盖情况：" + "，".join(parts) + "。")
    return notes


def _risk_notes(states: pd.DataFrame, candidates: pd.DataFrame, stats: pd.DataFrame) -> list[str]:
    notes = []
    if not states.empty:
        small = states[states["freq"].isin(["30分钟", "15分钟", "5分钟"])]
        weak_small = small[small["trend"] == "偏弱"]
        if not weak_small.empty:
            freqs = "、".join(weak_small["freq"].astype(str).tolist())
            notes.append(f"{freqs} 结构偏弱，说明短线仍有回踩压力。")
        risk_values = pd.to_numeric(states["risk_price"], errors="coerce").dropna()
        if not risk_values.empty:
            notes.append(f"最近结构风险位可先参考 {risk_values.max():.2f} 附近；跌破后需要重新评估买点有效性。")
    if not candidates.empty:
        latest_sell = _latest_candidate(candidates, {"一卖", "二卖", "三卖"})
        if latest_sell:
            notes.append(f"最近卖点候选为{latest_sell['freq']}{latest_sell['signal_type']}，不要把买点候选理解成无条件买入。")
    if stats.empty:
        notes.append("统计样本不足，当前结论只能作为结构观察。")
    return notes or ["暂无明显风险提示，但仍需结合仓位和止损纪律。"]


def _next_step_notes(summary: dict) -> list[str]:
    rating = summary.get("rating", "")
    if rating in {"积极观察", "中性偏强"}:
        return [
            "把该标的放入观察池，而不是直接满仓买入。",
            "等待小级别回踩不破风险位，或再次出现买点确认。",
            "后续用一批股票验证同类信号是否普遍有效，再决定是否写成交易规则。",
        ]
    return [
        "暂不把本次信号作为主动买入依据。",
        "继续观察是否出现更明确的大级别转强或小级别确认。",
        "优先做批量统计，避免只根据单只股票制定规则。",
    ]


def _latest_candidate(candidates: pd.DataFrame, signal_types: set[str]) -> dict | None:
    if candidates.empty:
        return None
    df = candidates[candidates["signal_type"].isin(signal_types)].copy()
    if df.empty:
        return None
    df["dt"] = pd.to_datetime(df["dt"])
    return dict(df.sort_values("dt").tail(1).iloc[0])


def _latest_signal_value(sigs: pd.DataFrame, contains: str) -> str:
    if sigs.empty:
        return "-"
    cols = [col for col in signal_columns(sigs) if contains in col]
    if not cols:
        return "-"
    return str(sigs.iloc[-1].get(cols[0], "-"))


def _simple_trend(df: pd.DataFrame) -> str:
    if len(df) < 30:
        return "样本不足"
    close = df["close"].astype(float)
    ma5 = close.tail(5).mean()
    ma20 = close.tail(20).mean()
    recent_high = close.tail(20).max()
    recent_low = close.tail(20).min()
    last = close.iloc[-1]
    if ma5 > ma20 and last >= recent_high * 0.97:
        return "偏强"
    if ma5 < ma20 and last <= recent_low * 1.03:
        return "偏弱"
    return "震荡"


def _risk_price(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    window = min(10, len(df))
    return round(float(df["low"].tail(window).min()), 3)


def latest_state(symbol: str, freq: str, bars: list, df: pd.DataFrame, sigs: pd.DataFrame) -> dict:
    close = float(df["close"].iloc[-1]) if not df.empty else None
    status = _latest_signal_value(sigs, "表里关系")
    ma5 = _latest_signal_value(sigs, "SMA#5")
    ma20 = _latest_signal_value(sigs, "SMA#20")
    candidates = extract_candidates(symbol, freq, sigs.tail(20) if not sigs.empty else sigs)
    recent_types = ",".join(candidates["signal_type"].tail(5).tolist()) if not candidates.empty else "-"
    structure = summarize_structure(bars)
    return {
        "symbol": symbol,
        "freq": freq,
        "dt": str(pd.to_datetime(df["dt"].iloc[-1])) if not df.empty else "",
        "close": close,
        "trend": _simple_trend(df),
        "bi_status": status,
        "ma5_state": ma5,
        "ma20_state": ma20,
        "fx_count": structure["fx_count"],
        "bi_count": structure["bi_count"],
        "last_bi_direction": structure["last_bi_direction"],
        "recent_candidates": recent_types,
        "risk_price": _risk_price(df),
    }


def summarize_structure(bars: list) -> dict:
    try:
        from czsc import CZSC

        c = CZSC(bars)
        fx_list = getattr(c, "fx_list", getattr(c, "fxs", [])) or []
        bi_list = getattr(c, "bi_list", getattr(c, "bis", [])) or []
        direction = "-"
        if bi_list:
            last = bi_list[-1]
            direction = str(getattr(last, "direction", "-"))
        return {"fx_count": len(fx_list), "bi_count": len(bi_list), "last_bi_direction": direction}
    except Exception:
        return {"fx_count": 0, "bi_count": 0, "last_bi_direction": "-"}


def build_decision_summary(states: pd.DataFrame, candidates: pd.DataFrame, stats: pd.DataFrame) -> dict:
    if states.empty:
        return {"rating": "无法判断", "score": 0, "action": "数据不足", "reason": "没有生成多级别状态。"}

    score = 50
    reasons = []
    big = states[states["freq"].isin(["日线", "60分钟"])].copy()
    small = states[states["freq"].isin(["30分钟", "15分钟", "5分钟"])].copy()
    strong_count = int((big["trend"] == "偏强").sum()) if not big.empty else 0
    weak_count = int((big["trend"] == "偏弱").sum()) if not big.empty else 0
    score += strong_count * 12
    score -= weak_count * 15
    if strong_count:
        reasons.append(f"大级别偏强数量 {strong_count}")
    if weak_count:
        reasons.append(f"大级别偏弱数量 {weak_count}")

    buy_candidates = candidates[candidates["signal_type"].isin(["一买", "二买", "三买"])] if not candidates.empty else pd.DataFrame()
    sell_candidates = candidates[candidates["signal_type"].isin(["一卖", "二卖", "三卖"])] if not candidates.empty else pd.DataFrame()
    if not buy_candidates.empty:
        latest_buy = buy_candidates.sort_values("dt").tail(1).iloc[0]
        score += 18
        reasons.append(f"最近出现{latest_buy['freq']}{latest_buy['signal_type']}")
    if not sell_candidates.empty:
        latest_sell = sell_candidates.sort_values("dt").tail(1).iloc[0]
        score -= 18
        reasons.append(f"最近出现{latest_sell['freq']}{latest_sell['signal_type']}")

    if not stats.empty:
        stat = stats[stats["signal_type"].isin(["一买", "二买", "三买"])].copy()
        if not stat.empty and "ret_5b_mean" in stat.columns:
            mean_ret = pd.to_numeric(stat["ret_5b_mean"], errors="coerce").mean()
            if pd.notna(mean_ret):
                score += 10 if mean_ret > 0 else -10
                reasons.append(f"买点信号未来5根均值 {mean_ret:.2%}")

    score = max(0, min(100, int(score)))
    if score >= 75:
        rating, action = "积极观察", "可进入重点观察池；若小级别回踩不破风险位，可考虑试仓。"
    elif score >= 60:
        rating, action = "中性偏强", "有结构机会，但应等待小级别确认，避免追高。"
    elif score >= 40:
        rating, action = "中性", "信号优势不明显，先观察结构演化。"
    else:
        rating, action = "谨慎", "结构或统计表现偏弱，暂不作为主动买入依据。"
    return {"rating": rating, "score": score, "action": action, "reason": "；".join(reasons) or "暂无显著买卖点共振。"}


def _write_report(run_dir: Path, cfg: CoreConfig, states: pd.DataFrame, candidates: pd.DataFrame, stats: pd.DataFrame, summary: dict) -> Path:
    path = run_dir / "v2_report.md"
    readable = summary.get("readable", {})
    lines = [
        "# Chan Strategy V2 Core Report",
        "",
        "## 定位",
        "",
        "V2 Core 是完整缠论策略的研究底座：多级别结构识别、候选买卖点提取、信号统计检验入口。",
        "它不是最终交易圣杯，也不会把所有缠论概念混成不可解释的黑箱。",
        "",
        "## 本次结论",
        "",
        f"- 综合评分: `{summary.get('score')}`",
        f"- 状态评级: `{summary.get('rating')}`",
        f"- 操作建议: {summary.get('action')}",
        f"- 主要依据: {summary.get('reason')}",
        f"- 运行口径: {summary.get('scope_note', '-')}",
        "",
        "## 给人的解读",
        "",
        readable.get("one_liner", "暂无白话解读。"),
        "",
        "### 结构怎么读",
        "",
    ]
    lines.extend([f"- {x}" for x in readable.get("structure", [])])
    lines.extend(
        [
            "",
            "### 最近信号怎么读",
            "",
        ]
    )
    lines.extend([f"- {x}" for x in readable.get("recent_signals", [])])
    lines.extend(
        [
            "",
            "### 历史统计怎么读",
            "",
        ]
    )
    lines.extend([f"- {x}" for x in readable.get("signal_stats", [])])
    lines.extend(
        [
            "",
            "### 风险提示",
            "",
        ]
    )
    lines.extend([f"- {x}" for x in readable.get("risk", [])])
    lines.extend(
        [
            "",
            "### 下一步动作",
            "",
        ]
    )
    lines.extend([f"- {x}" for x in readable.get("next_steps", [])])
    lines.extend(
        [
            "",
        "## 参数",
        "",
        f"- Mode: `{cfg.mode}`",
        f"- Symbols: `{cfg.symbols}`",
        f"- Date range: `{cfg.start_date}` to `{cfg.end_date}`",
        f"- Signal start: `{cfg.backtest_start}`",
        f"- Analysis freqs: `{cfg.analysis_freqs}`",
        f"- Speed mode: `{cfg.speed_mode}`",
        f"- FQ: `{cfg.fq}`",
        "",
        "## 原始数据表",
        "",
        "下面的表格是给后续量化检验用的原始结果。普通阅读可以优先看上面的白话解读。",
        "",
        "### 多级别状态",
        "",
        ]
    )
    if states.empty:
        lines.append("No states.")
    else:
        lines.extend(["```text", states.to_string(index=False), "```"])
    lines.extend(["", "### 最近候选买卖点", ""])
    recent = candidates.sort_values("dt").tail(30) if not candidates.empty else candidates
    if recent.empty:
        lines.append("No candidate signals.")
    else:
        lines.extend(["```text", recent.to_string(index=False), "```"])
    lines.extend(["", "### 信号统计", ""])
    if stats.empty:
        lines.append("No signal stats.")
    else:
        lines.extend(["```text", stats.to_string(index=False), "```"])
    lines.extend(
        [
            "",
            "## Data Integrity",
            "",
            f"- Real Tushare data used: {cfg.mode == 'tushare'}",
            f"- Sample data used/generated: {cfg.mode == 'mock'}",
            "- Strict real-data mode: True",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def run_core(cfg: CoreConfig) -> Path:
    if cfg.mode == "tushare":
        _require_tushare_token()
    speed_mode = _validate_speed_mode(cfg)
    symbols = _normalize_symbols(cfg.symbols)
    freqs = _analysis_freqs(cfg)

    run_id = "v2_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(cfg.output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "v2_config.json").write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")

    state_rows = []
    candidate_frames = []
    stat_frames = []
    errors = []
    total_tasks = len(symbols) * len(freqs)
    task_no = 0
    print(f"[v2-plan] symbols={len(symbols)} freqs={','.join(freqs)} speed={speed_mode} tasks={total_tasks}", flush=True)
    started = time.perf_counter()
    for symbol in symbols:
        for freq in freqs:
            task_no += 1
            task_started = time.perf_counter()
            try:
                print(f"[v2] analyzing {symbol} {freq} task={task_no}/{total_tasks}", flush=True)
                bars, kline = load_bars(symbol, freq, cfg)
                bars, kline = apply_speed_mode(symbol, freq, bars, kline, cfg)
                sigs = generate_signals(symbol, bars, freq, cfg)
                state_rows.append(latest_state(symbol, freq, bars, kline, sigs))
                candidates = extract_candidates(symbol, freq, sigs)
                if not candidates.empty:
                    candidate_frames.append(candidates)
                stats = signal_stats(symbol, freq, sigs)
                if not stats.empty:
                    stat_frames.append(stats)
                elapsed = time.perf_counter() - task_started
                print(f"[v2-done] {symbol} {freq} task={task_no}/{total_tasks} elapsed={elapsed:.1f}s", flush=True)
            except Exception as exc:  # noqa: BLE001
                errors.append({"symbol": symbol, "freq": freq, "error": f"{type(exc).__name__}: {exc}"})
                print(f"[v2-error] {symbol} {freq}: {type(exc).__name__}: {exc}")
                if cfg.mode == "tushare":
                    raise

    states = pd.DataFrame(state_rows)
    candidates = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
    stats = pd.concat(stat_frames, ignore_index=True) if stat_frames else pd.DataFrame()
    if not candidates.empty:
        candidates = candidates.sort_values(["symbol", "freq", "dt"]).reset_index(drop=True)
    if not stats.empty:
        stats = stats.sort_values(["symbol", "freq", "signal_type", "count"], ascending=[True, True, True, False]).reset_index(drop=True)

    summary = build_decision_summary(states, candidates, stats)
    summary["speed_mode"] = _validate_speed_mode(cfg)
    summary["scope_note"] = (
        "快速巡检模式会截取每个级别最近一段K线，用于日常持仓诊断；完整统计检验请使用 standard。"
        if summary["speed_mode"] == "quick"
        else "完整研究模式使用当前参数范围内的全量K线。"
    )
    summary["readable"] = build_readable_analysis(states, candidates, stats, summary)
    _write_csv(states, run_dir / "v2_latest_states.csv")
    _write_csv(candidates, run_dir / "v2_candidates.csv")
    _write_csv(stats, run_dir / "v2_signal_stats.csv")
    if errors:
        _write_csv(pd.DataFrame(errors), run_dir / "v2_errors.csv")
    (run_dir / "v2_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report = _write_report(run_dir, cfg, states, candidates, stats, summary)

    print(f"[v2-all-done] elapsed={time.perf_counter() - started:.1f}s", flush=True)
    print(f"Real Tushare data used: {cfg.mode == 'tushare'}")
    print(f"Sample data used/generated: {cfg.mode == 'mock'}")
    print(f"[done] report: {report}")
    return run_dir


def main() -> None:
    run_core(parse_args())


if __name__ == "__main__":
    main()
