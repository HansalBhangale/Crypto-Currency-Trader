from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("trader.spot_features")


def build_spot_features_5m(input_5m_csv: str, output_csv: str) -> None:
    """
    Input:  spot_5m_bars.csv with columns:
      bar_ts, symbol, open, high, low, close, mid_vwap, spread_bps_mean, spread_bps_p95, n_ticks

    Output: spot_features_5m.csv with columns (v0):
      bar_ts, symbol, close,
      r_5m, r_15m, r_1h, r_4h,
      vol_1h, vol_6h,
      spread_bps_mean, spread_bps_p95, n_ticks
    """
    in_path = Path(input_5m_csv)
    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        raise FileNotFoundError(f"Input not found: {in_path}")

    df = pd.read_csv(in_path)
    if df.empty:
        raise ValueError("Input 5m bars CSV is empty")

    df["bar_ts"] = pd.to_datetime(df["bar_ts"], utc=True)
    df = df.sort_values(["symbol", "bar_ts"])

    out = []
    for symbol, g in df.groupby("symbol", sort=True):
        g = g.copy()

        # Use close as reference price for returns
        close = g["close"].astype(float)
        logp = np.log(close.replace(0, np.nan))

        # log returns at different horizons (in 5m steps)
        g["r_5m"] = logp.diff(1)
        g["r_15m"] = logp.diff(3)
        g["r_1h"] = logp.diff(12)
        g["r_4h"] = logp.diff(48)

        # volatility of 5m returns (rolling std)
        r5 = g["r_5m"]
        g["vol_1h"] = r5.rolling(12, min_periods=12).std()
        g["vol_6h"] = r5.rolling(72, min_periods=72).std()

        out_cols = [
            "bar_ts", "symbol", "close",
            "r_5m", "r_15m", "r_1h", "r_4h",
            "vol_1h", "vol_6h",
            "spread_bps_mean", "spread_bps_p95", "n_ticks",
        ]
        out.append(g[out_cols])

    out_df = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    out_df.to_csv(out_path, index=False)
    log.info("Wrote 5m features: %s (rows=%d)", out_path, len(out_df))
