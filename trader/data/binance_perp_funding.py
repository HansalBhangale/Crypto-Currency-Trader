from __future__ import annotations

import asyncio
import csv
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("trader.binance_perp_funding")

BASE = "https://fapi.binance.com"


@dataclass
class FundingSnapshot:
    ts_ms: int
    symbol: str
    mark_price: float
    index_price: float
    funding_rate: float
    next_funding_time_ms: int


async def fetch_funding(symbol: str, client: httpx.AsyncClient) -> FundingSnapshot:
    # Premium index endpoint contains markPrice, indexPrice, lastFundingRate, nextFundingTime
    url = f"{BASE}/fapi/v1/premiumIndex"
    r = await client.get(url, params={"symbol": symbol}, timeout=10.0)
    r.raise_for_status()
    d = r.json()

    ts_ms = int(time.time() * 1000)
    return FundingSnapshot(
        ts_ms=ts_ms,
        symbol=symbol,
        mark_price=float(d["markPrice"]),
        index_price=float(d["indexPrice"]),
        funding_rate=float(d["lastFundingRate"]),
        next_funding_time_ms=int(d["nextFundingTime"]),
    )


async def stream_perp_funding(symbol: str, out_csv: str, poll_s: float = 60.0) -> None:
    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = out_path.exists()
    with out_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(
                ["ts_ms", "symbol", "mark_price", "index_price", "funding_rate", "next_funding_time_ms"]
            )

        backoff = 1.0
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    snap = await fetch_funding(symbol, client)
                    writer.writerow(
                        [
                            snap.ts_ms,
                            snap.symbol,
                            snap.mark_price,
                            snap.index_price,
                            snap.funding_rate,
                            snap.next_funding_time_ms,
                        ]
                    )
                    f.flush()

                    mins_to_funding = max(0.0, (snap.next_funding_time_ms - snap.ts_ms) / 60000.0)
                    log.info(
                        "FUND %s mark=%.2f idx=%.2f rate=%.6f next=%.1fmin",
                        snap.symbol,
                        snap.mark_price,
                        snap.index_price,
                        snap.funding_rate,
                        mins_to_funding,
                    )

                    backoff = 1.0
                    await asyncio.sleep(poll_s)

                except asyncio.CancelledError:
                    log.info("Funding poller cancelled, shutting down cleanly.")
                    return
                except KeyboardInterrupt:
                    log.info("KeyboardInterrupt received, shutting down cleanly.")
                    return
                except Exception as e:
                    log.warning("Funding fetch error: %s. Reconnecting in %.1fs...", repr(e), backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, 60.0)
