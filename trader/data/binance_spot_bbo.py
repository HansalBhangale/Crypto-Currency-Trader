from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import websockets


log = logging.getLogger("trader.binance_spot_bbo")


@dataclass
class BBO:
    ts_ms: int
    symbol: str
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


class BinanceSpotBBOStreamer:
    """
    Streams best bid/ask from Binance spot using bookTicker stream.
    Public endpoint, no API key required.

    Stream: wss://stream.binance.com:9443/ws/<symbol>@bookTicker
    """

    def __init__(self, symbol: str, out_csv: Path, heartbeat_s: float = 2.0, gap_s: float = 3.0) -> None:
        self.symbol = symbol.upper()
        self.symbol_stream = symbol.lower()
        self.url = f"wss://stream.binance.com:9443/ws/{self.symbol_stream}@bookTicker"
        self.out_csv = out_csv
        self.heartbeat_s = heartbeat_s
        self.gap_s = gap_s

        self._last_msg_ts: Optional[float] = None
        self._last_write_sec: Optional[int] = None
        self._last_log_sec: Optional[int] = None

    async def run(self) -> None:
        self.out_csv.parent.mkdir(parents=True, exist_ok=True)

        file_exists = self.out_csv.exists()
        with self.out_csv.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["ts_ms", "symbol", "bid", "ask", "mid"])

            backoff = 1.0
            while True:
                try:
                    async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                        log.info("Connected spot BBO stream: %s", self.url)
                        backoff = 1.0  # reset after successful connect

                        while True:
                            msg = await ws.recv()
                            now = time.time()
                            self._last_msg_ts = now

                            data = json.loads(msg)
                            bid = float(data["b"])
                            ask = float(data["a"])
                            ts_ms = int(now * 1000)

                            bbo = BBO(ts_ms=ts_ms, symbol=self.symbol, bid=bid, ask=ask)

                            # Write only once per second (1-second snapshots)
                            sec = int(now)
                            if self._last_write_sec is None or sec != self._last_write_sec:
                                self._last_write_sec = sec
                                writer.writerow([bbo.ts_ms, bbo.symbol, bbo.bid, bbo.ask, bbo.mid])
                                f.flush()

                            # Monitoring logs (once every 10 seconds)
                            if sec % 10 == 0 and self._last_log_sec != sec:
                                self._last_log_sec = sec
                                log.info("BBO %s bid=%.2f ask=%.2f mid=%.2f", bbo.symbol, bbo.bid, bbo.ask, bbo.mid)

                            await self._check_health(now)

                except asyncio.CancelledError:
                    log.info("Streamer cancelled, shutting down cleanly.")
                    return
                except KeyboardInterrupt:
                    log.info("KeyboardInterrupt received, shutting down cleanly.")
                    return
                except Exception as e:
                    log.warning("WebSocket error: %s. Reconnecting in %.1fs...", repr(e), backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, 30.0)

    async def _check_health(self, now: float) -> None:
        if self._last_msg_ts is None:
            return
        age = now - self._last_msg_ts
        if age > self.gap_s:
            log.warning("DATA GAP: no WS update for %.2fs (>%.2fs)", age, self.gap_s)
        elif age > self.heartbeat_s:
            log.warning("STALE: last WS update %.2fs ago (>%.2fs)", age, self.heartbeat_s)


async def stream_spot_bbo(symbol: str, out_csv: str) -> None:
    streamer = BinanceSpotBBOStreamer(symbol=symbol, out_csv=Path(out_csv))
    await streamer.run()
