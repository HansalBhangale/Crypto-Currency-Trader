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

    Output: spot_features_5m.csv with columns (v1):
      bar_ts, symbol, close,
      r_5m, r_15m, r_1h, r_4h,
      vol_1h, vol_6h,
      spread_bps_mean, spread_bps_p95, n_ticks,
      dq_score, unsafe, caution
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

        # -----------------------------
        # Data Quality (DQ)
        # -----------------------------
        # tick completeness: expect ~300 1s samples per 5m
        expected_ticks = 300.0
        g["tick_score"] = (g["n_ticks"].astype(float) / expected_ticks).clip(0.0, 1.0)

        # spread sanity (bps). Conservative threshold for BTC.
        g["spread_ok"] = (g["spread_bps_mean"].astype(float) <= 5.0).astype(int)

        # jump/outlier sanity based on 5m log return magnitude
        # Treat NaN return (first bar) as OK
        jump_ok = (g["r_5m"].isna()) | (g["r_5m"].abs() <= 0.02)
        g["jump_ok"] = jump_ok.astype(int)

        # DQ score: continuous tick completeness + hard checks
        g["dq_score"] = 0.4 * g["tick_score"] + 0.3 * g["spread_ok"] + 0.3 * g["jump_ok"]

        # Hard-stop UNSAFE only if truly bad (avoid blocking baseline too often)
        g["unsafe"] = (g["dq_score"] < 0.75).astype(int)

        # Caution flag (ok to trade if you choose, but track it)
        g["caution"] = ((g["dq_score"] >= 0.75) & (g["dq_score"] < 0.90)).astype(int)

        out_cols = [
            "bar_ts", "symbol", "close",
            "r_5m", "r_15m", "r_1h", "r_4h",
            "vol_1h", "vol_6h",
            "spread_bps_mean", "spread_bps_p95", "n_ticks",
            "tick_score",
            "dq_score", "unsafe", "caution",
]

        out.append(g[out_cols])

    out_df = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    out_df.to_csv(out_path, index=False)
    log.info("Wrote 5m features: %s (rows=%d)", out_path, len(out_df))
