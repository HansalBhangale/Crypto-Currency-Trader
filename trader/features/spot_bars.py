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
