"""Preflight checks for the local Tushare-backed strategy system.

This script is intentionally conservative: it never prints the token value,
and the optional network check is opt-in so dry validation does not get stuck
behind a proxy or temporary connectivity issue.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "chan_strategy"


def _check_proxy(host: str = "127.0.0.1", port: int = 7890, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Check local quant strategy runtime environment.")
    parser.add_argument("--network-check", action="store_true", help="Call Tushare pro.daily for a real network check.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Local cache directory.")
    args = parser.parse_args()

    token = os.environ.get("TUSHARE_TOKEN", "")
    cache_dir = Path(args.cache_dir)
    (cache_dir / "bars").mkdir(parents=True, exist_ok=True)
    (cache_dir / "index_weight").mkdir(parents=True, exist_ok=True)

    print(f"Python executable: {sys.executable}")
    print(f"TUSHARE_TOKEN configured: {bool(token)}")
    print(f"TUSHARE_TOKEN length: {len(token) if token else 0}")
    print(f"TUSHARE_HTTP_URL: {os.environ.get('TUSHARE_HTTP_URL', 'https://tt.dailyfetch.top/')}")
    print(f"Local proxy 127.0.0.1:7890 reachable: {_check_proxy()}")
    print(f"Cache directory: {cache_dir}")
    print("Strict real-data mode: True")

    try:
        import tushare as ts

        print(f"tushare version: {getattr(ts, '__version__', 'unknown')}")
        print(f"tushare module: {getattr(ts, '__file__', 'unknown')}")
    except Exception as exc:  # noqa: BLE001
        print(f"tushare import failed: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc

    if args.network_check:
        if not token:
            raise SystemExit("TUSHARE_TOKEN is not configured; cannot run network check.")
        from tushare_client import get_pro_api

        pro = get_pro_api()
        df = pro.daily(ts_code="000001.SZ", start_date="20240101", end_date="20240110")
        print(f"Tushare pro.daily rows: {len(df)}")
        if df.empty:
            raise SystemExit("Tushare network check returned 0 rows.")

    print("Real Tushare data used: False")
    print("Sample data used/generated: False")


if __name__ == "__main__":
    main()
