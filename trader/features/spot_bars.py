from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger("trader.spot_bars")


def build_spot_1m_bars(input_csv: str, output_csv: str) -> None:
    """
    Reads spot_bbo.csv (1s snapshots) and builds 1-minute OHLC bars on mid price.

    Input columns: ts_ms, symbol, bid, ask, mid
    Output columns:
      minute_ts, symbol, open, high, low, close, mid_vwap,
      spread_bps_mean, spread_bps_p95, n_ticks
    """
    in_path = Path(input_csv)
    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        raise FileNotFoundError(f"Input not found: {in_path}")

    df = pd.read_csv(in_path)
    if df.empty:
        raise ValueError("Input CSV is empty")

    # Timestamp handling
    df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df["minute_ts"] = df["ts"].dt.floor("min")

    # Spread in bps
    df["spread_bps"] = ((df["ask"] - df["bid"]) / df["mid"]) * 10_000.0

    # Group to 1-minute bars
    g = df.groupby(["minute_ts", "symbol"], sort=True)

    bars = pd.DataFrame(
        {
            "open": g["mid"].first(),
            "high": g["mid"].max(),
            "low": g["mid"].min(),
            "close": g["mid"].last(),
            "mid_vwap": g["mid"].mean(),  # approximate VWAP since we don't have trade sizes here
            "spread_bps_mean": g["spread_bps"].mean(),
            "spread_bps_p95": g["spread_bps"].quantile(0.95),
            "n_ticks": g["mid"].count(),
        }
    ).reset_index()

    # Write
    bars.to_csv(out_path, index=False)
    log.info("Wrote 1m bars: %s (rows=%d)", out_path, len(bars))

def build_spot_5m_bars(input_1m_csv: str, output_csv: str) -> None:
    """
    Reads spot_1m_bars.csv and builds 5-minute OHLC bars.

    Input columns: minute_ts, symbol, open, high, low, close, mid_vwap, spread_bps_mean, spread_bps_p95, n_ticks
    Output columns:
      bar_ts, symbol, open, high, low, close, mid_vwap,
      spread_bps_mean, spread_bps_p95, n_ticks
    """
    in_path = Path(input_1m_csv)
    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        raise FileNotFoundError(f"Input not found: {in_path}")

    df = pd.read_csv(in_path)
    if df.empty:
        raise ValueError("Input 1m bars CSV is empty")

    # Ensure datetime
    df["minute_ts"] = pd.to_datetime(df["minute_ts"], utc=True)
    df = df.sort_values(["symbol", "minute_ts"])

    out_rows = []
    for symbol, g in df.groupby("symbol", sort=True):
        g = g.set_index("minute_ts")

        # OHLC aggregation on close -> standard bar chaining from 1m bars
        o = g["open"].resample("5min").first()
        h = g["high"].resample("5min").max()
        l = g["low"].resample("5min").min()
        c = g["close"].resample("5min").last()

        # mid_vwap: weighted by n_ticks (better than raw mean)
        mid_vwap = (g["mid_vwap"] * g["n_ticks"]).resample("5min").sum() / g["n_ticks"].resample("5min").sum()

        # spreads: weighted mean by n_ticks; p95: take max of 1m p95s (conservative)
        spread_mean = (g["spread_bps_mean"] * g["n_ticks"]).resample("5min").sum() / g["n_ticks"].resample("5min").sum()
        spread_p95 = g["spread_bps_p95"].resample("5min").max()

        n = g["n_ticks"].resample("5min").sum()

        bars = pd.DataFrame(
            {
                "bar_ts": o.index,
                "symbol": symbol,
                "open": o.values,
                "high": h.values,
                "low": l.values,
                "close": c.values,
                "mid_vwap": mid_vwap.values,
                "spread_bps_mean": spread_mean.values,
                "spread_bps_p95": spread_p95.values,
                "n_ticks": n.values,
            }
        )

        bars = bars.dropna(subset=["open", "high", "low", "close"])
        out_rows.append(bars)

    out_df = pd.concat(out_rows, ignore_index=True) if out_rows else pd.DataFrame()
    out_df.to_csv(out_path, index=False)
    log.info("Wrote 5m bars: %s (rows=%d)", out_path, len(out_df))

