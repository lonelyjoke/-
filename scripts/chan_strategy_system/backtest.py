"""Backtest a first-version Chan strategy on a configurable stock universe.

The default strategy follows the examples in docs/examples:

- open long: 30-minute third-buy signal, with limit-up exclusion
- exit long: 30-minute BI status turns down
- portfolio: average all per-symbol position weights, then evaluate with wbt

Real-data mode is strict: Tushare failures stop the run instead of falling back
to generated data. Mock mode exists only to validate the local pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent


def _avoid_unbuilt_source_package() -> None:
    """Avoid importing the in-repo ``czsc`` package when native extension is absent.

    Running this script from the repository root puts the source tree ahead of
    site-packages. That is fine after ``maturin develop`` succeeds, but before
    the Rust extension is built the source tree contains only
    ``czsc/_native/__init__.pyi`` and cannot satisfy ``import czsc``.

    The strategy app is user-facing, so prefer an installed wheel in
    site-packages when the local native extension is not present.
    """
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

try:
    import czsc
    from czsc import CzscStrategyBase, Event, Position, WeightBacktest, format_standard_kline
except ModuleNotFoundError as exc:
    missing = exc.name or "czsc"
    raise SystemExit(
        "\n".join(
            [
                f"Missing Python package: {missing}",
                "The strategy UI should run with an environment that has prebuilt czsc and wbt installed.",
                "Recommended one-time install for your current interpreter:",
                r"  D:\Anaconda\python.exe -m pip install -U czsc wbt",
                "Then restart the UI and run the analysis again.",
            ]
        )
    ) from exc


DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "chan_strategy"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "reports" / "chan_strategy"


@dataclass
class RunConfig:
    mode: str = "mock"
    pool: str = "custom"
    index_code: str = "000905.SH"
    symbols: str = "000001.SZ,000002.SZ"
    symbols_file: str | None = None
    start_date: str = "20200101"
    end_date: str = "20240601"
    backtest_start: str = "20200701"
    base_freq: str = "30分钟"
    limit: int = 20
    fee_rate: float = 0.0002
    fq: str = "后复权"
    max_workers: int = 1
    cache_dir: str = str(DEFAULT_CACHE_DIR)
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    seed: int = 42


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value)


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip()
    if not symbol:
        raise ValueError("Empty symbol is not allowed.")
    if "#" in symbol:
        return symbol
    from tushare_client import infer_asset

    return f"{symbol}#{infer_asset(symbol)}"


def _asset_symbol(symbol: str) -> str:
    return symbol.split("#", 1)[0]


def _read_config(path: str | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_args() -> RunConfig:
    parser = argparse.ArgumentParser(description="Run the first Chan strategy backtest.")
    parser.add_argument("--config", help="Optional JSON config file.")
    parser.add_argument("--mode", choices=["mock", "tushare"], help="Data mode.")
    parser.add_argument("--pool", choices=["custom", "csi500-history"], help="Universe type.")
    parser.add_argument("--index-code", help="Index code for csi500-history; default 000905.SH.")
    parser.add_argument("--symbols", help="Comma-separated symbols for custom pool.")
    parser.add_argument("--symbols-file", help="CSV/TXT file with a symbol column or one symbol per line.")
    parser.add_argument("--start-date", help="Data start date, e.g. 20200101.")
    parser.add_argument("--end-date", help="Data end date, e.g. 20240601.")
    parser.add_argument("--backtest-start", help="Backtest signal start date after warm-up.")
    parser.add_argument("--base-freq", help="Base frequency, e.g. 30分钟.")
    parser.add_argument("--limit", type=int, help="Maximum number of symbols to run; 0 means all.")
    parser.add_argument("--fee-rate", type=float, help="Single-side fee rate.")
    parser.add_argument("--fq", help="Adjustment mode, e.g. 后复权.")
    parser.add_argument("--max-workers", type=int, help="Reserved for future parallel fetch.")
    parser.add_argument("--cache-dir", help="Cache directory.")
    parser.add_argument("--output-dir", help="Output directory.")
    parser.add_argument("--seed", type=int, help="Mock data seed.")
    args = parser.parse_args()

    merged = asdict(RunConfig())
    merged.update(_read_config(args.config))
    for key, value in vars(args).items():
        if key == "config":
            continue
        if value is not None:
            merged[key] = value
    return RunConfig(**merged)


def build_third_buy_position(symbol: str, base_freq: str) -> Position:
    exit_event = Event.load(
        {
            "name": "笔向下_平多",
            "operate": "平多",
            "signals_all": [f"{base_freq}_D1_表里关系V230101_向下_任意_任意_0"],
        }
    )
    open_event = Event.load(
        {
            "name": "三买V230228_开多",
            "operate": "开多",
            "signals_all": [f"{base_freq}_D1_三买辅助V230228_三买_任意_任意_0"],
            "signals_not": [f"{base_freq}_D1_涨跌停V230331_涨停_任意_任意_0"],
        }
    )
    return Position(
        name=f"{base_freq}_三买V230228",
        symbol=symbol,
        opens=[open_event],
        exits=[exit_event],
        interval=3600 * 4,
        timeout=16 * 30,
        stop_loss=300,
        t0=False,
    )


class ThirdBuyStrategy(CzscStrategyBase):
    @property
    def positions(self) -> list[Position]:
        return [build_third_buy_position(self.symbol, self.kwargs.get("base_freq", "30分钟"))]


def _load_symbols_from_file(path: str) -> list[str]:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        df = pd.read_csv(p)
        if "symbol" not in df.columns:
            raise ValueError(f"{path} must contain a 'symbol' column.")
        return [str(x) for x in df["symbol"].dropna().tolist()]
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_custom_symbols(cfg: RunConfig) -> list[str]:
    symbols: list[str] = []
    if cfg.symbols_file:
        symbols.extend(_load_symbols_from_file(cfg.symbols_file))
    if cfg.symbols:
        symbols.extend([x.strip() for x in cfg.symbols.split(",") if x.strip()])
    unique = []
    seen = set()
    for sym in symbols:
        norm = _normalize_symbol(sym)
        if norm not in seen:
            unique.append(norm)
            seen.add(norm)
    if not unique:
        raise ValueError("No symbols provided for custom pool.")
    return unique


def _require_tushare_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not configured. Please set it locally before real-data runs.")
    print(f"TUSHARE_TOKEN configured: True; length={len(token)}")
    return token


def load_index_weight(cfg: RunConfig) -> pd.DataFrame:
    _require_tushare_token()
    from tushare_client import get_pro_api

    pro = get_pro_api()
    cache_dir = Path(cfg.cache_dir) / "index_weight"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cfg.index_code}_{cfg.start_date}_{cfg.end_date}.parquet"
    if cache_file.exists():
        df = pd.read_parquet(cache_file)
    else:
        df = pro.index_weight(index_code=cfg.index_code, start_date=cfg.start_date, end_date=cfg.end_date)
        if df.empty:
            raise RuntimeError(f"Tushare index_weight returned 0 rows for {cfg.index_code}.")
        df.to_parquet(cache_file, index=False)
    required = {"trade_date", "con_code"}
    if not required <= set(df.columns):
        raise RuntimeError(f"index_weight data missing columns: {required - set(df.columns)}")
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.sort_values(["trade_date", "con_code"]).reset_index(drop=True)


def load_pool_symbols(cfg: RunConfig) -> tuple[list[str], pd.DataFrame | None]:
    if cfg.pool == "custom":
        symbols = load_custom_symbols(cfg)
        return _limit_symbols(symbols, cfg.limit), None

    if cfg.mode != "tushare":
        raise ValueError("csi500-history pool requires --mode tushare.")
    weights = load_index_weight(cfg)
    symbols = [f"{x}#E" for x in sorted(weights["con_code"].dropna().unique().tolist())]
    return _limit_symbols(symbols, cfg.limit), weights


def _limit_symbols(symbols: list[str], limit: int) -> list[str]:
    if limit and limit > 0:
        return symbols[:limit]
    return symbols


def load_mock_bars(symbol: str, cfg: RunConfig) -> list:
    from czsc.mock import generate_symbol_kines

    ts_code = _asset_symbol(symbol)
    df = generate_symbol_kines(ts_code, cfg.base_freq, cfg.start_date, cfg.end_date, seed=cfg.seed)
    return format_standard_kline(df, freq=cfg.base_freq)


def load_tushare_bars(symbol: str, cfg: RunConfig) -> list:
    _require_tushare_token()
    from tushare_client import fetch_pro_bar_minutes, init_tushare

    ts_code, asset = symbol.split("#", 1)
    freq = cfg.base_freq.replace("分钟", "min")
    cache_dir = Path(cfg.cache_dir) / "bars" / _safe_name(cfg.base_freq)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{ts_code}_{asset}_{cfg.start_date}_{cfg.end_date}_{_safe_name(cfg.fq)}.parquet"
    if cache_file.exists():
        df = pd.read_parquet(cache_file)
    else:
        adj = "qfq" if cfg.fq == "前复权" else "hfq"
        pro = init_tushare()
        df = fetch_pro_bar_minutes(pro, ts_code, sdt=cfg.start_date, edt=cfg.end_date, freq=freq, asset=asset, adj=adj)
        if df.empty:
            raise RuntimeError(f"No minute bars returned for {symbol}.")
        df.to_parquet(cache_file, index=False)
    df = normalize_kline_dtypes(df)
    return format_standard_kline(df, freq=cfg.base_freq)


def normalize_kline_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize kline dtypes for both local source builds and PyPI rs-czsc.

    The PyPI ``czsc==0.10.x`` path calls ``rs_czsc._format_standard_kline_bytes``,
    which is strict about Float64 numeric columns. Cached parquet files created
    before this normalization may contain float32, so normalize on every read.
    """
    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"])
    df["symbol"] = df["symbol"].astype(str)
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    return df[["symbol", "dt", "open", "high", "low", "close", "vol", "amount"]]


def holds_to_weight_df(holds: pd.DataFrame) -> pd.DataFrame:
    if holds.empty:
        return pd.DataFrame(columns=["dt", "symbol", "weight", "price"])
    df = holds[["dt", "symbol", "pos", "price"]].rename(columns={"pos": "weight"})
    df["dt"] = normalize_dt_series(df["dt"])
    if df.duplicated(subset=["dt", "symbol"]).any():
        df = df.groupby(["dt", "symbol"], as_index=False).agg(weight=("weight", "mean"), price=("price", "first"))
    return df[["dt", "symbol", "weight", "price"]]


def normalize_dt_series(series: pd.Series) -> pd.Series:
    """Normalize datetime values from both old and new czsc result schemas."""
    if pd.api.types.is_numeric_dtype(series):
        non_null = series.dropna()
        if non_null.empty:
            return pd.to_datetime(series)
        sample = float(non_null.iloc[0])
        if sample > 1e17:
            return pd.to_datetime(series, unit="ns")
        if sample > 1e14:
            return pd.to_datetime(series, unit="us")
        if sample > 1e11:
            return pd.to_datetime(series, unit="ms")
        return pd.to_datetime(series, unit="s")
    parsed = pd.to_datetime(series, errors="coerce")
    # Old czsc may stringify second timestamps, e.g. "1594636200".
    missing = parsed.isna()
    if missing.any():
        numeric = pd.to_numeric(series[missing], errors="coerce")
        parsed.loc[missing] = normalize_dt_series(numeric)
    return parsed


def _object_to_dict(obj) -> dict:
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "dump"):
        try:
            data = obj.dump()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {}


def extract_result_frames(result, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Extract holds and pairs from both new and old czsc backtest results."""
    if hasattr(result, "holds_df"):
        holds = result.holds_df()
        pairs = result.pairs_df() if hasattr(result, "pairs_df") else pd.DataFrame()
        return holds, pairs

    hold_rows: list[dict] = []
    pair_rows: list[dict] = []
    positions = getattr(result, "positions", []) or []
    for pos in positions:
        pos_name = getattr(pos, "name", "")
        for hold in getattr(pos, "holds", []) or []:
            row = _object_to_dict(hold)
            if not row:
                continue
            row.setdefault("symbol", symbol)
            row.setdefault("pos_name", pos_name)
            hold_rows.append(row)
        for pair in getattr(pos, "pairs", []) or []:
            row = _object_to_dict(pair)
            if not row:
                continue
            row.setdefault("symbol", symbol)
            row.setdefault("pos_name", pos_name)
            pair_rows.append(row)

    holds = pd.DataFrame(hold_rows)
    pairs = pd.DataFrame(pair_rows)
    if not holds.empty:
        if "symbol" not in holds.columns:
            holds["symbol"] = symbol
        if "price" not in holds.columns:
            for candidate in ["close", "最新价", "价格"]:
                if candidate in holds.columns:
                    holds["price"] = holds[candidate]
                    break
        if "pos" not in holds.columns:
            for candidate in ["position", "weight", "持仓"]:
                if candidate in holds.columns:
                    holds["pos"] = holds[candidate]
                    break

    missing = [] if holds.empty else [col for col in ["dt", "symbol", "pos", "price"] if col not in holds.columns]
    if missing:
        available = ", ".join(map(str, holds.columns.tolist()))
        raise RuntimeError(f"Cannot extract weight table from CzscTrader holds; missing {missing}; available columns: {available}")
    return holds, pairs


def build_membership_filter(index_weight: pd.DataFrame | None):
    if index_weight is None or index_weight.empty:
        return None
    grouped = index_weight.groupby("trade_date")["con_code"].apply(lambda s: set(s.tolist())).sort_index()
    dates = list(grouped.index)
    sets = list(grouped.values)

    def allowed(symbol: str, dt) -> bool:
        key = pd.to_datetime(dt).normalize()
        pos = bisect_right(dates, key) - 1
        if pos < 0:
            return False
        return _asset_symbol(symbol) in sets[pos]

    return allowed


def apply_membership_filter(dfw: pd.DataFrame, index_weight: pd.DataFrame | None) -> pd.DataFrame:
    checker = build_membership_filter(index_weight)
    if checker is None or dfw.empty:
        return dfw
    keep = [checker(row.symbol, row.dt) for row in dfw.itertuples(index=False)]
    return dfw.loc[keep].reset_index(drop=True)


def run_one_symbol(symbol: str, cfg: RunConfig) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    print(f"[symbol] running {symbol}")
    bars = load_mock_bars(symbol, cfg) if cfg.mode == "mock" else load_tushare_bars(symbol, cfg)
    if not bars:
        raise RuntimeError(f"No bars for {symbol}")
    strategy = ThirdBuyStrategy(symbol=_asset_symbol(symbol), base_freq=cfg.base_freq)
    result = strategy.backtest(bars, sdt=cfg.backtest_start)
    holds, pairs = extract_result_frames(result, _asset_symbol(symbol))
    weights = holds_to_weight_df(holds)
    if not pairs.empty:
        pairs = pairs.copy()
        for col in pairs.columns:
            if "时间" in str(col) or str(col).lower() in {"dt", "sdt", "edt", "open_dt", "close_dt"}:
                try:
                    pairs[col] = normalize_dt_series(pairs[col])
                except Exception:
                    pass
        pairs["symbol"] = _asset_symbol(symbol)
    stats = {
        "symbol": _asset_symbol(symbol),
        "bars": len(bars),
        "holds": len(holds),
        "pairs": len(pairs),
        "nonzero_weight_rows": int((weights["weight"] != 0).sum()) if not weights.empty else 0,
    }
    return weights, stats, pairs


def _portfolio_weights(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    pieces = [x for x in frames if x is not None and not x.empty]
    if not pieces:
        return pd.DataFrame(columns=["dt", "symbol", "weight", "price"])
    df = pd.concat(pieces, ignore_index=True)
    df["dt"] = pd.to_datetime(df["dt"])
    return df.sort_values(["dt", "symbol"]).reset_index(drop=True)


def _write_markdown_report(run_dir: Path, cfg: RunConfig, symbols: list[str], per_symbol: pd.DataFrame, stats: dict) -> Path:
    path = run_dir / "report.md"
    lines = [
        "# Chan Strategy Backtest Report",
        "",
        f"- Run mode: `{cfg.mode}`",
        f"- Pool: `{cfg.pool}`",
        f"- Index code: `{cfg.index_code}`",
        f"- Date range: `{cfg.start_date}` to `{cfg.end_date}`",
        f"- Backtest start: `{cfg.backtest_start}`",
        f"- Base frequency: `{cfg.base_freq}`",
        f"- Symbols requested: `{len(symbols)}`",
        f"- Fee rate: `{cfg.fee_rate}`",
        "",
        "## Strategy",
        "",
        "- Open: `{freq}_D1_三买辅助V230228_三买_任意_任意_0`",
        "- Exit: `{freq}_D1_表里关系V230101_向下_任意_任意_0`",
        "- Guard: do not open on `{freq}_D1_涨跌停V230331_涨停_任意_任意_0`",
        "",
        "## Portfolio Stats",
        "",
    ]
    if stats:
        for key, value in stats.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- No portfolio stats generated.")

    lines.extend(["", "## Per Symbol Summary", ""])
    if per_symbol.empty:
        lines.append("No per-symbol summary.")
    else:
        lines.append("```text")
        lines.append(per_symbol.to_string(index=False))
        lines.append("```")
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


def run_backtest(cfg: RunConfig) -> Path:
    if cfg.mode == "tushare":
        _require_tushare_token()

    symbols, index_weight = load_pool_symbols(cfg)
    if not symbols:
        raise RuntimeError("Universe is empty.")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(cfg.output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame({"symbol": symbols}).to_csv(run_dir / "symbols.csv", index=False)

    frames: list[pd.DataFrame] = []
    pair_frames: list[pd.DataFrame] = []
    rows: list[dict] = []
    errors: list[dict] = []
    for symbol in symbols:
        try:
            weights, stats, pairs = run_one_symbol(symbol, cfg)
            frames.append(weights)
            if not pairs.empty:
                pair_frames.append(pairs)
            rows.append(stats)
        except Exception as exc:  # noqa: BLE001
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[error] {symbol}: {type(exc).__name__}: {exc}")
            if cfg.mode == "tushare":
                raise

    per_symbol = pd.DataFrame(rows)
    per_symbol.to_csv(run_dir / "per_symbol_summary.csv", index=False)
    if errors:
        pd.DataFrame(errors).to_csv(run_dir / "errors.csv", index=False)

    portfolio = _portfolio_weights(frames)
    portfolio = apply_membership_filter(portfolio, index_weight)
    portfolio.to_csv(run_dir / "portfolio_weights.csv", index=False)

    if pair_frames:
        pd.concat(pair_frames, ignore_index=True).to_csv(run_dir / "pairs.csv", index=False)

    portfolio_stats: dict = {}
    if not portfolio.empty and (portfolio["weight"] != 0).any():
        wb = WeightBacktest(portfolio, fee_rate=cfg.fee_rate, weight_type="ts", yearly_days=252)
        portfolio_stats = dict(wb.stats)
        pd.Series(portfolio_stats).to_csv(run_dir / "portfolio_stats.csv", header=["value"])
        try:
            from wbt import generate_backtest_report

            generate_backtest_report(
                df=portfolio,
                output_path=str(run_dir / "portfolio_report.html"),
                title="Chan Strategy Backtest",
                fee_rate=cfg.fee_rate,
                weight_type="ts",
                yearly_days=252,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] HTML report generation failed: {type(exc).__name__}: {exc}")
    else:
        print("[warn] no nonzero portfolio weights; portfolio stats skipped.")

    report = _write_markdown_report(run_dir, cfg, symbols, per_symbol, portfolio_stats)
    print(f"Real Tushare data used: {cfg.mode == 'tushare'}")
    print(f"Sample data used/generated: {cfg.mode == 'mock'}")
    print(f"[done] report: {report}")
    return run_dir


def main() -> None:
    cfg = parse_args()
    run_backtest(cfg)


if __name__ == "__main__":
    main()
