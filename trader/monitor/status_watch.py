from __future__ import annotations

import asyncio
import csv
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("trader.status_watch")


def _tail_last_data_row(path: Path) -> Optional[dict]:
    """
    Reads header + last data row from CSV without pandas.
    Returns dict or None.
    """
    if not path.exists():
        return None

    # If file is tiny, this is fine. For large files we still try to be cheap:
    # read from end in chunks.
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return None

            chunk = 4096
            data = b""
            pos = size
            while pos > 0 and data.count(b"\n") < 3:
                read_size = min(chunk, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + data

        lines = [ln.decode("utf-8", errors="ignore").strip() for ln in data.splitlines() if ln.strip()]
        if len(lines) < 2:
            return None

        # Find header line (first line in file). We'll read just that separately.
        with path.open("r", encoding="utf-8") as tf:
            header = tf.readline().strip()
        if not header:
            return None

        header_cols = next(csv.reader([header]))
        last_line = lines[-1]
        last_cols = next(csv.reader([last_line]))

        if len(last_cols) != len(header_cols):
            return None

        return dict(zip(header_cols, last_cols))
    except Exception:
        return None


def _age_from_ts_ms(ts_ms: Optional[int]) -> Optional[float]:
    if ts_ms is None:
        return None
    return time.time() - (ts_ms / 1000.0)


def _age_from_iso_utc(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        # e.g. "2025-12-25 13:15:00+00:00"
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


@dataclass
class WatchPaths:
    raw_dir: Path
    derived_dir: Path


async def run_status_watch(paths: WatchPaths, every_s: float = 60.0) -> None:
    spot_bbo = paths.raw_dir / "spot_bbo.csv"
    perp_fund = paths.raw_dir / "perp_funding.csv"
    basis_1m = paths.derived_dir / "basis_1m.csv"
    spot_feat = paths.derived_dir / "spot_features_5m.csv"
    baseline = paths.derived_dir / "baseline_signals.csv"

    while True:
        try:
            now = int(time.time() * 1000)

            r_spot = _tail_last_data_row(spot_bbo)
            r_perp = _tail_last_data_row(perp_fund)
            r_basis = _tail_last_data_row(basis_1m)
            r_feat = _tail_last_data_row(spot_feat)
            r_sig = _tail_last_data_row(baseline)

            spot_age = _age_from_ts_ms(int(r_spot["ts_ms"])) if r_spot and "ts_ms" in r_spot else None
            perp_age = _age_from_ts_ms(int(r_perp["ts_ms"])) if r_perp and "ts_ms" in r_perp else None
            basis_age = _age_from_ts_ms(int(r_basis["ts_ms"])) if r_basis and "ts_ms" in r_basis else None
            feat_age = _age_from_iso_utc(r_feat.get("bar_ts")) if r_feat else None
            sig_age = _age_from_ts_ms(int(r_sig["ts_ms"])) if r_sig and "ts_ms" in r_sig else None

            unsafe = r_feat.get("unsafe") if r_feat else None
            action = r_sig.get("action") if r_sig else None
            reason = r_sig.get("reason") if r_sig else None

            # Simple staleness thresholds (tune later)
            ok_spot = (spot_age is not None and spot_age <= 10)
            ok_perp = (perp_age is not None and perp_age <= 120)
            ok_basis = (basis_age is not None and basis_age <= 120)
            ok_feat = (feat_age is not None and feat_age <= 900)  # 15 min
            ok_sig = (sig_age is not None and sig_age <= 120)

            overall_ok = all([ok_spot, ok_perp, ok_basis, ok_feat, ok_sig])
            level = logging.INFO if overall_ok else logging.WARNING

            log.log(
                level,
                "HB spot_age=%ss perp_age=%ss basis_age=%ss feat_age=%ss sig_age=%ss unsafe=%s action=%s reason=%s",
                None if spot_age is None else f"{spot_age:.0f}",
                None if perp_age is None else f"{perp_age:.0f}",
                None if basis_age is None else f"{basis_age:.0f}",
                None if feat_age is None else f"{feat_age:.0f}",
                None if sig_age is None else f"{sig_age:.0f}",
                unsafe,
                action,
                reason,
            )

            await asyncio.sleep(every_s)

        except asyncio.CancelledError:
            log.info("status_watch cancelled, shutting down cleanly.")
            return
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt received, shutting down cleanly.")
            return
        except Exception as e:
            log.warning("status_watch error: %s", repr(e))
            await asyncio.sleep(5.0)
