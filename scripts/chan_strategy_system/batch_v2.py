"""Batch runner for V2 Chan research.

This script is designed for hypothesis testing across a stock universe, such
as the current HS300 constituents. It runs ``chan_core.run_core`` one symbol at
a time, writes per-symbol progress markers, and aggregates the generated V2
CSV/JSON artifacts into batch-level files.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from chan_core import CoreConfig, run_core  # noqa: E402
from tushare_client import get_pro_api  # noqa: E402


DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "chan_strategy"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "reports" / "chan_strategy"
POOL_INDEX_CODES = {
    "hs300": "000300.SH",
    "csi500": "000905.SH",
    "custom": "",
}


@dataclass
class BatchConfig:
    mode: str = "tushare"
    pool: str = "hs300"
    index_code: str = "000300.SH"
    symbols: str = ""
    start_date: str = "20230101"
    end_date: str = "20260612"
    backtest_start: str = "20240901"
    analysis_freqs: str = "日线,60分钟,30分钟"
    speed_mode: str = "standard"
    fq: str = "前复权"
    cache_dir: str = str(DEFAULT_CACHE_DIR)
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    resume_dir: str = ""
    limit: int = 0
    resume: bool = True
    fail_fast: bool = False


def parse_args() -> BatchConfig:
    parser = argparse.ArgumentParser(description="Run V2 Chan research on a stock pool.")
    parser.add_argument("--mode", choices=["tushare", "mock"], default=None)
    parser.add_argument("--pool", choices=["hs300", "csi500", "custom"], default=None)
    parser.add_argument("--index-code")
    parser.add_argument("--symbols", help="Comma-separated symbols for --pool custom.")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--backtest-start")
    parser.add_argument("--analysis-freqs")
    parser.add_argument("--speed-mode", choices=["quick", "standard"], default=None)
    parser.add_argument("--fq")
    parser.add_argument("--cache-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--resume-dir", help="Existing v2_batch_* directory to continue.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--rerun", action="store_true", help="Ignore completed progress markers.")
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    merged = asdict(BatchConfig())
    for key, value in vars(args).items():
        if key == "rerun":
            merged["resume"] = not value
        elif value is not None:
            merged[key] = value
    if not merged.get("index_code"):
        merged["index_code"] = POOL_INDEX_CODES.get(merged["pool"], "")
    return BatchConfig(**merged)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if "#" in symbol:
        symbol = symbol.split("#", 1)[0]
    if not symbol:
        return ""
    return symbol


def _parse_custom_symbols(symbols: str) -> list[str]:
    values = []
    seen = set()
    for raw in symbols.split(","):
        symbol = _normalize_symbol(raw)
        if not symbol or symbol in seen:
            continue
        values.append(symbol)
        seen.add(symbol)
    if not values:
        raise ValueError("--pool custom requires --symbols.")
    return values


def load_index_members(cfg: BatchConfig) -> tuple[list[str], pd.DataFrame]:
    if cfg.pool == "custom":
        symbols = _parse_custom_symbols(cfg.symbols)
        return _limit_symbols(symbols, cfg.limit), pd.DataFrame()
    if cfg.mode != "tushare":
        raise ValueError("Index pools require --mode tushare.")

    index_code = cfg.index_code or POOL_INDEX_CODES[cfg.pool]
    cache_dir = Path(cfg.cache_dir) / "index_weight"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{index_code}_{cfg.start_date}_{cfg.end_date}.parquet"
    if cache_file.exists():
        print(f"[batch-index-cache-hit] {cache_file}", flush=True)
        weights = pd.read_parquet(cache_file)
    else:
        print(f"[batch-index-fetch] {index_code} {cfg.start_date}-{cfg.end_date}", flush=True)
        pro = get_pro_api()
        weights = pro.index_weight(index_code=index_code, start_date=cfg.start_date, end_date=cfg.end_date)
        if weights is None or weights.empty:
            raise RuntimeError(f"Tushare index_weight returned 0 rows for {index_code}.")
        weights.to_parquet(cache_file, index=False)
        print(f"[batch-index-cache-write] rows={len(weights)} {cache_file}", flush=True)

    required = {"trade_date", "con_code"}
    if not required <= set(weights.columns):
        raise RuntimeError(f"index_weight data missing columns: {required - set(weights.columns)}")
    weights = weights.copy()
    weights["trade_date"] = pd.to_datetime(weights["trade_date"])
    latest_date = weights["trade_date"].max()
    latest = weights[weights["trade_date"] == latest_date]
    symbols = sorted(latest["con_code"].dropna().astype(str).str.upper().unique().tolist())
    symbols = _limit_symbols(symbols, cfg.limit)
    print(f"[batch-index-members] index={index_code} date={latest_date.date()} symbols={len(symbols)}", flush=True)
    return symbols, weights.sort_values(["trade_date", "con_code"]).reset_index(drop=True)


def _limit_symbols(symbols: list[str], limit: int) -> list[str]:
    if limit and limit > 0:
        return symbols[:limit]
    return symbols


def _new_batch_dir(cfg: BatchConfig) -> Path:
    if cfg.resume_dir:
        batch_dir = Path(cfg.resume_dir)
        if not batch_dir.is_absolute():
            batch_dir = Path(cfg.output_dir) / batch_dir
        if not batch_dir.exists() or not batch_dir.is_dir():
            raise FileNotFoundError(f"--resume-dir does not exist: {batch_dir}")
        if not batch_dir.name.startswith("v2_batch_"):
            raise ValueError(f"--resume-dir must point to a v2_batch_* directory: {batch_dir}")
        _append_resume_record(batch_dir, cfg)
        print(f"[batch-resume-dir] {batch_dir}", flush=True)
        return batch_dir
    run_id = "v2_batch_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = Path(cfg.output_dir) / run_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "batch_config.json").write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    return batch_dir


def _append_resume_record(batch_dir: Path, cfg: BatchConfig) -> None:
    config_path = batch_dir / "batch_config.json"
    existing = _read_json_file(config_path)
    record = {
        "resumed_at": datetime.now().isoformat(timespec="seconds"),
        "args": asdict(cfg),
    }
    if existing:
        history = existing.get("resume_history", [])
        history.append(record)
        existing["resume_history"] = history
        existing["last_resume_at"] = record["resumed_at"]
        config_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        payload = asdict(cfg)
        payload["resume_history"] = [record]
        config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _progress_path(batch_dir: Path, symbol: str) -> Path:
    progress_dir = batch_dir / "progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    return progress_dir / f"{_safe_name(symbol)}.json"


def _load_marker(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_marker(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_progress_markers(batch_dir: Path) -> dict[str, dict]:
    progress_dir = batch_dir / "progress"
    if not progress_dir.exists():
        return {}
    markers = {}
    for path in progress_dir.glob("*.json"):
        marker = _load_marker(path)
        symbol = marker.get("symbol")
        if symbol:
            markers[symbol] = marker
    return markers


def _core_config_for_symbol(symbol: str, cfg: BatchConfig, output_dir: Path) -> CoreConfig:
    return CoreConfig(
        mode=cfg.mode,
        symbols=symbol,
        start_date=cfg.start_date,
        end_date=cfg.end_date,
        backtest_start=cfg.backtest_start,
        analysis_freqs=cfg.analysis_freqs,
        speed_mode=cfg.speed_mode,
        fq=cfg.fq,
        cache_dir=cfg.cache_dir,
        output_dir=str(output_dir),
    )


def run_symbol(symbol: str, cfg: BatchConfig, batch_dir: Path, index: int, total: int) -> dict:
    marker_path = _progress_path(batch_dir, symbol)
    marker = _load_marker(marker_path)
    if cfg.resume and marker.get("status") == "done" and marker.get("run_dir"):
        run_dir = Path(marker["run_dir"])
        if run_dir.exists():
            print(f"[batch-skip] {symbol} {index}/{total} {run_dir}", flush=True)
            return marker

    print(f"[batch] running {symbol} {index}/{total}", flush=True)
    started = time.perf_counter()
    marker = {
        "symbol": symbol,
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_marker(marker_path, marker)
    try:
        run_dir = run_core(_core_config_for_symbol(symbol, cfg, batch_dir / "single_runs"))
        elapsed = round(time.perf_counter() - started, 2)
        marker = {
            "symbol": symbol,
            "status": "done",
            "run_dir": str(run_dir),
            "elapsed_seconds": elapsed,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        print(f"[batch-done] {symbol} {index}/{total} elapsed={elapsed}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        elapsed = round(time.perf_counter() - started, 2)
        marker = {
            "symbol": symbol,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": elapsed,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        print(f"[batch-error] {symbol} {index}/{total} {marker['error']}", flush=True)
        if cfg.fail_fast:
            _write_marker(marker_path, marker)
            raise
    _write_marker(marker_path, marker)
    return marker


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def _read_summary(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def aggregate_batch(batch_dir: Path, markers: list[dict], cfg: BatchConfig, index_weight: pd.DataFrame) -> None:
    summary_rows = []
    state_frames = []
    candidate_frames = []
    stat_frames = []
    for marker in markers:
        symbol = marker.get("symbol", "")
        row = {
            "symbol": symbol,
            "status": marker.get("status", ""),
            "elapsed_seconds": marker.get("elapsed_seconds", ""),
            "run_dir": marker.get("run_dir", ""),
            "error": marker.get("error", ""),
        }
        run_dir = Path(marker.get("run_dir", ""))
        if marker.get("status") == "done" and run_dir.exists():
            summary = _read_summary(run_dir / "v2_summary.json")
            row.update(
                {
                    "score": summary.get("score", ""),
                    "rating": summary.get("rating", ""),
                    "action": summary.get("action", ""),
                    "reason": summary.get("reason", ""),
                }
            )
            states = _read_csv(run_dir / "v2_latest_states.csv")
            candidates = _read_csv(run_dir / "v2_candidates.csv")
            stats = _read_csv(run_dir / "v2_signal_stats.csv")
            if not states.empty:
                state_frames.append(states)
            if not candidates.empty:
                candidate_frames.append(candidates)
            if not stats.empty:
                stat_frames.append(stats)
        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    states_df = pd.concat(state_frames, ignore_index=True) if state_frames else pd.DataFrame()
    candidates_df = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
    stats_df = pd.concat(stat_frames, ignore_index=True) if stat_frames else pd.DataFrame()

    summary_df.to_csv(batch_dir / "batch_summary.csv", index=False, encoding="utf-8-sig")
    states_df.to_csv(batch_dir / "batch_latest_states.csv", index=False, encoding="utf-8-sig")
    candidates_df.to_csv(batch_dir / "batch_candidates.csv", index=False, encoding="utf-8-sig")
    stats_df.to_csv(batch_dir / "batch_signal_stats.csv", index=False, encoding="utf-8-sig")
    if not index_weight.empty:
        index_weight.to_csv(batch_dir / "batch_index_weight.csv", index=False, encoding="utf-8-sig")
    _write_batch_report(batch_dir, cfg, summary_df, stats_df)


def _write_batch_report(batch_dir: Path, cfg: BatchConfig, summary: pd.DataFrame, stats: pd.DataFrame) -> None:
    done = int((summary["status"] == "done").sum()) if not summary.empty else 0
    errors = int((summary["status"] == "error").sum()) if not summary.empty else 0
    lines = [
        "# V2 Batch Research Report",
        "",
        "## 运行口径",
        "",
        f"- Pool: `{cfg.pool}`",
        f"- Index code: `{cfg.index_code}`",
        f"- Date range: `{cfg.start_date}` to `{cfg.end_date}`",
        f"- Signal start: `{cfg.backtest_start}`",
        f"- Analysis freqs: `{cfg.analysis_freqs}`",
        f"- Speed mode: `{cfg.speed_mode}`",
        f"- FQ: `{cfg.fq}`",
        "",
        "## 完成情况",
        "",
        f"- 完成股票数: `{done}`",
        f"- 失败股票数: `{errors}`",
        f"- 汇总文件: `batch_summary.csv`, `batch_signal_stats.csv`, `batch_candidates.csv`, `batch_latest_states.csv`",
        "",
    ]
    if not summary.empty and "score" in summary.columns:
        ranked = summary[summary["status"] == "done"].copy()
        if not ranked.empty:
            ranked["score"] = pd.to_numeric(ranked["score"], errors="coerce")
            top = ranked.sort_values("score", ascending=False).head(10)
            lines.extend(["## 评分靠前标的", "", "```text", top[["symbol", "score", "rating", "reason"]].to_string(index=False), "```", ""])
    if not stats.empty:
        lines.extend(["## 信号统计提示", ""])
        lines.append("批量统计的重点不是单只股票高分，而是观察同类信号在股票池中的总体分布。")
        lines.append("下一步可以基于 `batch_signal_stats.csv` 按信号类型、周期和市场环境做分组检验。")
    (batch_dir / "batch_report.md").write_text("\n".join(lines), encoding="utf-8")


def run_batch(cfg: BatchConfig) -> Path:
    if cfg.pool in POOL_INDEX_CODES and cfg.pool != "custom" and not cfg.index_code:
        cfg.index_code = POOL_INDEX_CODES[cfg.pool]
    symbols, index_weight = load_index_members(cfg)
    batch_dir = _new_batch_dir(cfg)
    total = len(symbols)
    print(f"[batch-plan] pool={cfg.pool} symbols={total} freqs={cfg.analysis_freqs} speed={cfg.speed_mode}", flush=True)
    markers_by_symbol = load_progress_markers(batch_dir)
    started = time.perf_counter()
    for index, symbol in enumerate(symbols, start=1):
        marker = run_symbol(symbol, cfg, batch_dir, index, total)
        markers_by_symbol[symbol] = marker
        aggregate_batch(batch_dir, _ordered_markers(symbols, markers_by_symbol), cfg, index_weight)
    aggregate_batch(batch_dir, _ordered_markers(symbols, markers_by_symbol), cfg, index_weight)
    print(f"[batch-all-done] dir={batch_dir} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    print(f"[done] batch report: {batch_dir / 'batch_report.md'}", flush=True)
    return batch_dir


def _ordered_markers(symbols: list[str], markers_by_symbol: dict[str, dict]) -> list[dict]:
    symbol_set = set(symbols)
    ordered = [markers_by_symbol[symbol] for symbol in symbols if symbol in markers_by_symbol]
    extras = [marker for symbol, marker in markers_by_symbol.items() if symbol not in symbol_set]
    return ordered + extras


def main() -> None:
    run_batch(parse_args())


if __name__ == "__main__":
    main()
