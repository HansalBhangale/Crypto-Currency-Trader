from __future__ import annotations

import asyncio
import csv
import logging
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger("trader.basis_1m")


def _read_last_row(csv_path: Path) -> dict | None:
    if not csv_path.exists():
        return None
    try:
        df = pd.read_csv(csv_path)
        if df.empty:
            return None
        return df.iloc[-1].to_dict()
    except Exception as e:
        log.warning("Failed reading %s: %s", csv_path, repr(e))
        return None


async def stream_basis_1m(
    spot_bbo_csv: str,
    perp_funding_csv: str,
    out_csv: str,
    poll_s: float = 60.0,
) -> None:
    spot_path = Path(spot_bbo_csv)
    perp_path = Path(perp_funding_csv)
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
                    "spot_mid",
                    "perp_mark",
                    "perp_index",
                    "basis_bps",
                    "funding_rate_8h",
                    "minutes_to_funding",
                ]
            )

        while True:
            try:
                spot = _read_last_row(spot_path)
                perp = _read_last_row(perp_path)

                if spot is None or perp is None:
                    log.warning(
                        "Waiting for inputs... spot_exists=%s perp_exists=%s",
                        spot_path.exists(),
                        perp_path.exists(),
                    )
                    await asyncio.sleep(poll_s)
                    continue

                # parse
                ts_ms = int(time.time() * 1000)
                symbol = str(perp["symbol"])

                spot_mid = float(spot["mid"])
                perp_mark = float(perp["mark_price"])
                perp_index = float(perp["index_price"])
                funding_rate = float(perp["funding_rate"])

                next_funding_time_ms = int(perp["next_funding_time_ms"])
                minutes_to_funding = max(0.0, (next_funding_time_ms - ts_ms) / 60000.0)

                basis_bps = ((perp_mark - spot_mid) / spot_mid) * 10_000.0

                writer.writerow(
                    [
                        ts_ms,
                        symbol,
                        spot_mid,
                        perp_mark,
                        perp_index,
                        basis_bps,
                        funding_rate,
                        minutes_to_funding,
                    ]
                )
                f.flush()

                log.info(
                    "BASIS %s spot=%.2f perp=%.2f basis=%.2fbps fund=%.6f ttf=%.1fmin",
                    symbol,
                    spot_mid,
                    perp_mark,
                    basis_bps,
                    funding_rate,
                    minutes_to_funding,
                )

                await asyncio.sleep(poll_s)

            except asyncio.CancelledError:
                log.info("basis_1m cancelled, shutting down cleanly.")
                return
            except KeyboardInterrupt:
                log.info("KeyboardInterrupt received, shutting down cleanly.")
                return
            except Exception as e:
                log.warning("basis_1m error: %s", repr(e))
                await asyncio.sleep(5.0)
