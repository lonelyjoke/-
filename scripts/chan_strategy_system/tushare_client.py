"""Tushare initialization helpers for the Chan strategy system.

Token handling rule:

- Read token from ``TUSHARE_TOKEN`` only.
- Never hard-code or print the token value.
- Use ``TUSHARE_HTTP_URL`` when provided; otherwise use the configured
  alternate endpoint required by this local setup.

PowerShell example:

```powershell
$env:TUSHARE_TOKEN="your_token"
$env:TUSHARE_HTTP_URL="https://tt.dailyfetch.top/"
```
"""

from __future__ import annotations

from datetime import timedelta
import os

import pandas as pd


DEFAULT_TUSHARE_HTTP_URL = "https://tt.dailyfetch.top/"
FUND_PREFIXES = (
    "510",
    "511",
    "512",
    "513",
    "515",
    "516",
    "517",
    "518",
    "520",
    "588",
    "159",
    "160",
    "161",
    "162",
    "163",
    "164",
    "165",
    "166",
    "167",
    "168",
    "169",
)


def get_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not configured.")
    return token


def get_http_url() -> str:
    return os.environ.get("TUSHARE_HTTP_URL", DEFAULT_TUSHARE_HTTP_URL).strip() or DEFAULT_TUSHARE_HTTP_URL


def infer_asset(ts_code: str) -> str:
    """Infer Tushare ``asset`` type from a code.

    ``E`` is stock, ``FD`` is fund / ETF. Users normally type only
    ``002202.SZ`` or ``510300.SH`` in the UI, so the strategy layer needs a
    small deterministic mapper before calling ``ts.pro_bar``.
    """
    code = ts_code.split("#", 1)[0].split(".", 1)[0]
    return "FD" if code.startswith(FUND_PREFIXES) else "E"


def get_pro_api():
    """Create a configured Tushare Pro API object.

    The private ``_DataApi__http_url`` assignment follows the user's local
    Tushare endpoint guide. It is centralized here so other scripts do not
    duplicate token or endpoint initialization.
    """
    import tushare as ts

    token = get_token()
    pro = ts.pro_api(token)
    pro._DataApi__http_url = get_http_url()
    return pro


def init_tushare():
    """Initialize Tushare in memory and return the Pro API.

    This deliberately avoids ``tushare.set_token`` and ``czsc.set_url_token``
    because both persist credentials under the user home directory. The strategy
    system keeps credentials in environment variables only.
    """
    return get_pro_api()


def _load_adjust_factors(pro, ts_code: str, asset: str, sdt: str, edt: str) -> pd.DataFrame:
    if asset == "E":
        df = pro.adj_factor(ts_code=ts_code, start_date=sdt, end_date=edt)
    elif asset == "FD":
        df = pro.fund_adj(ts_code=ts_code, start_date=sdt, end_date=edt)
    else:
        df = pd.DataFrame()
    if df is None or df.empty or not {"trade_date", "adj_factor"} <= set(df.columns):
        return pd.DataFrame()
    return df[["trade_date", "adj_factor"]].copy()


def fetch_pro_bar_minutes(pro, ts_code: str, sdt, edt, freq: str = "30min", asset: str = "E", adj: str | None = None):
    """Fetch Tushare minute bars using ``ts.pro_bar(api=pro, ...)``.

    Returns a standard czsc DataFrame with columns:
    ``symbol, dt, open, high, low, close, vol, amount``.
    """
    import tushare as ts

    dt_fmt = "%Y%m%d"
    sdt = pd.to_datetime(sdt).strftime(dt_fmt)
    edt = pd.to_datetime(edt).strftime(dt_fmt)

    klines = []
    end_dt = pd.to_datetime(edt)
    dt1 = pd.to_datetime(sdt)
    delta = timedelta(days=40 * int(freq.replace("min", "")))
    dt2 = dt1 + delta

    while dt1 < end_dt:
        df = ts.pro_bar(
            api=pro,
            ts_code=ts_code,
            asset=asset,
            freq=freq,
            start_date=dt1.strftime(dt_fmt),
            end_date=dt2.strftime(dt_fmt),
        )
        dt1 = dt2 - pd.Timedelta(days=1)
        dt2 = dt1 + delta
        if df is not None and len(df) > 0:
            klines.append(df)

    if not klines:
        return pd.DataFrame(columns=["symbol", "dt", "open", "high", "low", "close", "vol", "amount"])

    kline = pd.concat(klines, ignore_index=True)
    kline = kline.drop_duplicates("trade_time").sort_values("trade_time", ascending=True, ignore_index=True)
    kline["trade_time"] = pd.to_datetime(kline["trade_time"])
    float_cols = ["open", "close", "high", "low", "vol", "amount"]
    # Keep Float64: rs-czsc's Arrow/Polars bridge rejects float32 columns.
    kline[float_cols] = kline[float_cols].astype("float64")

    # Match the repository connector convention: drop the 09:30 minute bar and zero-volume bars.
    keep = ~((kline["trade_time"].dt.hour == 9) & (kline["trade_time"].dt.minute == 30))
    kline = kline[keep & (kline["vol"] > 0)].copy()
    kline = kline[(kline["trade_time"] >= pd.to_datetime(sdt)) & (kline["trade_time"] <= pd.to_datetime(edt))]
    kline = kline.reset_index(drop=True)
    kline["trade_date"] = kline["trade_time"].dt.strftime(dt_fmt)

    factors = _load_adjust_factors(pro, ts_code, asset, sdt, edt)
    if not factors.empty:
        date_frame = pd.DataFrame({"trade_date": kline["trade_date"].unique().tolist()})
        factors = date_frame.merge(factors, on="trade_date", how="left").ffill().bfill()
        factors = factors.sort_values("trade_date", ignore_index=True)

    if not factors.empty and adj == "qfq":
        latest_factor = factors.iloc[-1]["adj_factor"]
        adj_map = {row["trade_date"]: row["adj_factor"] for _, row in factors.iterrows()}
        for col in ["open", "close", "high", "low"]:
            kline[col] = kline.apply(lambda x, col=col: x[col] * adj_map[x["trade_date"]] / latest_factor, axis=1)

    if not factors.empty and adj == "hfq":
        adj_map = {row["trade_date"]: row["adj_factor"] for _, row in factors.iterrows()}
        for col in ["open", "close", "high", "low"]:
            kline[col] = kline.apply(lambda x, col=col: x[col] * adj_map[x["trade_date"]], axis=1)

    kline["symbol"] = ts_code
    kline["dt"] = pd.to_datetime(kline["trade_time"])
    return kline[["symbol", "dt", "open", "high", "low", "close", "vol", "amount"]].copy()


def fetch_pro_bar_daily(pro, ts_code: str, sdt, edt, asset: str = "E", adj: str | None = None) -> pd.DataFrame:
    """Fetch daily bars using the same configured Tushare ``pro`` client."""
    import tushare as ts

    dt_fmt = "%Y%m%d"
    sdt = pd.to_datetime(sdt).strftime(dt_fmt)
    edt = pd.to_datetime(edt).strftime(dt_fmt)
    # Fetch raw bars first and apply adjustment locally. Tushare's internal
    # pro_bar adjustment path can raise opaque errors for ETF/fund symbols when
    # it looks for stock ``adj_factor`` columns.
    df = ts.pro_bar(api=pro, ts_code=ts_code, asset=asset, freq="D", start_date=sdt, end_date=edt)
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "dt", "open", "high", "low", "close", "vol", "amount"])

    df = df.drop_duplicates("trade_date").sort_values("trade_date", ascending=True, ignore_index=True)
    factors = _load_adjust_factors(pro, ts_code, asset, sdt, edt)
    if not factors.empty and adj in {"qfq", "hfq"}:
        date_frame = pd.DataFrame({"trade_date": df["trade_date"].unique().tolist()})
        factors = date_frame.merge(factors, on="trade_date", how="left").ffill().bfill()
        factor_map = {row["trade_date"]: row["adj_factor"] for _, row in factors.iterrows()}
        if adj == "qfq":
            latest_factor = factors.iloc[-1]["adj_factor"]
            for col in ["open", "close", "high", "low"]:
                df[col] = df.apply(lambda x, col=col: x[col] * factor_map[x["trade_date"]] / latest_factor, axis=1)
        if adj == "hfq":
            for col in ["open", "close", "high", "low"]:
                df[col] = df.apply(lambda x, col=col: x[col] * factor_map[x["trade_date"]], axis=1)

    df["symbol"] = ts_code
    df["dt"] = pd.to_datetime(df["trade_date"])
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    return df[["symbol", "dt", "open", "high", "low", "close", "vol", "amount"]].copy()
