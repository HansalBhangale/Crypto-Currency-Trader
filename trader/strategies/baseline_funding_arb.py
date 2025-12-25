from __future__ import annotations

import asyncio
import csv
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

log = logging.getLogger("trader.baseline_signals")


@dataclass
class BaselineConfig:
    funding_thr: float = 0.0002      # 0.02% per 8h
    basis_max_bps: float = 10.0      # don't enter if basis is too wide
    min_minutes_to_funding: float = 30.0  # avoid too close to funding in v0
    size_btc: float = 0.01           # small preview size (no trading yet)


def _read_last_row(csv_path: Path) -> dict | None:
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    if df.empty:
        return None
    return df.iloc[-1].to_dict()


def decide_signal(unsafe: int, basis_bps: float, funding_rate_8h: float, minutes_to_funding: float, cfg: BaselineConfig):
    if unsafe == 1:
        return ("FLAT", 0.0, "unsafe=1")
    if minutes_to_funding < cfg.min_minutes_to_funding:
        return ("FLAT", 0.0, f"too_close_to_funding<{cfg.min_minutes_to_funding}m")
    if (funding_rate_8h >= cfg.funding_thr) and (basis_bps <= cfg.basis_max_bps):
        return ("LONG_SPOT_SHORT_PERP", cfg.size_btc, "funding_high_and_basis_ok")
    return ("FLAT", 0.0, "conditions_not_met")


async def stream_baseline_signals(
    spot_features_5m_csv: str,
    basis_1m_csv: str,
    out_csv: str,
    poll_s: float = 60.0,
    cfg: BaselineConfig | None = None,
) -> None:
    cfg = cfg or BaselineConfig()

    spot_path = Path(spot_features_5m_csv)
    basis_path = Path(basis_1m_csv)
    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = out_path.exists()
    with out_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(
                [
                    "ts_ms",
                    "symbol",
                    "unsafe",
                    "basis_bps",
                    "funding_rate_8h",
                    "minutes_to_funding",
                    "action",
                    "size_btc",
                    "reason",
                ]
            )

        while True:
            try:
                spot = _read_last_row(spot_path)
                basis = _read_last_row(basis_path)

                if spot is None or basis is None:
                    log.warning(
                        "Waiting for inputs... spot_exists=%s basis_exists=%s",
                        spot_path.exists(),
                        basis_path.exists(),
                    )
                    await asyncio.sleep(poll_s)
                    continue

                ts_ms = int(time.time() * 1000)
                symbol = str(basis["symbol"])

                unsafe = int(spot.get("unsafe", 1))
                basis_bps = float(basis["basis_bps"])
                funding_rate_8h = float(basis["funding_rate_8h"])
                minutes_to_funding = float(basis["minutes_to_funding"])

                action, size_btc, reason = decide_signal(unsafe, basis_bps, funding_rate_8h, minutes_to_funding, cfg)

                writer.writerow(
                    [ts_ms, symbol, unsafe, basis_bps, funding_rate_8h, minutes_to_funding, action, size_btc, reason]
                )
                f.flush()

                log.info(
                    "SIG %s unsafe=%d basis=%.2fbps fund=%.6f ttf=%.1f -> %s size=%.4f (%s)",
                    symbol,
                    unsafe,
                    basis_bps,
                    funding_rate_8h,
                    minutes_to_funding,
                    action,
                    size_btc,
                    reason,
                )

                await asyncio.sleep(poll_s)

            except asyncio.CancelledError:
                log.info("baseline_signals cancelled, shutting down cleanly.")
                return
            except KeyboardInterrupt:
                log.info("KeyboardInterrupt received, shutting down cleanly.")
                return
            except Exception as e:
                log.warning("baseline_signals error: %s", repr(e))
                await asyncio.sleep(5.0)
