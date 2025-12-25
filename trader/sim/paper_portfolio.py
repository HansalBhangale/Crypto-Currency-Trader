from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger("trader.paper_portfolio")


def _read_last_row(csv_path: Path) -> dict | None:
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    if df.empty:
        return None
    return df.iloc[-1].to_dict()


@dataclass
class PaperState:
    cash_usdt: float = 10_000.0
    q_spot_btc: float = 0.0
    q_perp_btc: float = 0.0
    perp_entry: Optional[float] = None  # avg entry price for current perp position
    last_signal_ts_ms: Optional[int] = None


def _load_state(path: Path, initial_cash: float) -> PaperState:
    if not path.exists():
        return PaperState(cash_usdt=float(initial_cash))
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        st = PaperState(
            cash_usdt=float(d.get("cash_usdt", initial_cash)),
            q_spot_btc=float(d.get("q_spot_btc", 0.0)),
            q_perp_btc=float(d.get("q_perp_btc", 0.0)),
            perp_entry=d.get("perp_entry", None),
            last_signal_ts_ms=d.get("last_signal_ts_ms", None),
        )
        if st.perp_entry is not None:
            st.perp_entry = float(st.perp_entry)
        return st
    except Exception as e:
        log.warning("Failed loading state %s: %s (starting fresh)", path, repr(e))
        return PaperState(cash_usdt=float(initial_cash))


def _save_state(path: Path, st: PaperState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(st), indent=2), encoding="utf-8")


def _apply_spot_trade(st: PaperState, target_q_spot: float, spot_mid: float) -> None:
    dq = target_q_spot - st.q_spot_btc
    if abs(dq) < 1e-12:
        return
    # Buy spot -> spend cash; Sell spot -> receive cash
    st.cash_usdt -= dq * spot_mid
    st.q_spot_btc = target_q_spot


def _apply_perp_trade(st: PaperState, target_q_perp: float, perp_mark: float) -> None:
    q0 = st.q_perp_btc
    if abs(target_q_perp - q0) < 1e-12:
        return

    # Helper: realize pnl on a closed quantity "closed_qty" at mark
    def realize_pnl(closed_qty: float) -> None:
        if st.perp_entry is None:
            return
        pnl = closed_qty * (perp_mark - st.perp_entry)
        st.cash_usdt += pnl

    # If currently flat
    if abs(q0) < 1e-12:
        st.q_perp_btc = target_q_perp
        st.perp_entry = perp_mark if abs(target_q_perp) > 1e-12 else None
        return

    # If target is flat: close all
    if abs(target_q_perp) < 1e-12:
        realize_pnl(q0)  # close whole position
        st.q_perp_btc = 0.0
        st.perp_entry = None
        return

    # Same sign?
    same_sign = (q0 > 0 and target_q_perp > 0) or (q0 < 0 and target_q_perp < 0)

    if same_sign:
        # Increasing position size -> update avg entry
        if abs(target_q_perp) > abs(q0):
            add_qty = target_q_perp - q0
            # weighted avg
            st.perp_entry = (q0 * st.perp_entry + add_qty * perp_mark) / target_q_perp  # type: ignore[operator]
            st.q_perp_btc = target_q_perp
            return

        # Reducing position size -> realize pnl on reduced part
        if abs(target_q_perp) < abs(q0):
            closed_qty = q0 - target_q_perp
            realize_pnl(closed_qty)
            st.q_perp_btc = target_q_perp
            if abs(st.q_perp_btc) < 1e-12:
                st.q_perp_btc = 0.0
                st.perp_entry = None
            return

    # Flip sign: close all then open new
    realize_pnl(q0)
    st.q_perp_btc = target_q_perp
    st.perp_entry = perp_mark


def _equity(st: PaperState, spot_mid: float, perp_mark: float) -> float:
    spot_val = st.q_spot_btc * spot_mid
    # Perp equity contribution = unrealized PnL only
    perp_unreal = 0.0
    if st.perp_entry is not None and abs(st.q_perp_btc) > 1e-12:
        perp_unreal = st.q_perp_btc * (perp_mark - st.perp_entry)
    return st.cash_usdt + spot_val + perp_unreal


async def run_paper_portfolio(
    baseline_signals_csv: str,
    basis_1m_csv: str,
    out_csv: str,
    state_json: str,
    initial_cash_usdt: float = 10_000.0,
    poll_s: float = 60.0,
) -> None:
    sig_path = Path(baseline_signals_csv)
    basis_path = Path(basis_1m_csv)
    out_path = Path(out_csv)
    st_path = Path(state_json)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    st = _load_state(st_path, initial_cash_usdt)

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
                    "basis_bps",
                    "funding_rate_8h",
                    "minutes_to_funding",
                    "signal_action",
                    "signal_size_btc",
                    "q_spot_btc",
                    "q_perp_btc",
                    "perp_entry",
                    "cash_usdt",
                    "equity_usdt",
                    "net_btc",
                    "reason",
                ]
            )

        while True:
            try:
                sig = _read_last_row(sig_path)
                basis = _read_last_row(basis_path)

                if sig is None or basis is None:
                    log.warning(
                        "Waiting for inputs... sig_exists=%s basis_exists=%s",
                        sig_path.exists(),
                        basis_path.exists(),
                    )
                    await asyncio.sleep(poll_s)
                    continue

                ts_ms = int(time.time() * 1000)
                symbol = str(basis["symbol"])

                spot_mid = float(basis["spot_mid"])
                perp_mark = float(basis["perp_mark"])
                basis_bps = float(basis["basis_bps"])
                funding_rate_8h = float(basis["funding_rate_8h"])
                minutes_to_funding = float(basis["minutes_to_funding"])

                action = str(sig["action"])
                size_btc = float(sig["size_btc"])
                reason = str(sig.get("reason", ""))

                sig_ts = int(sig["ts_ms"]) if "ts_ms" in sig else None

                # Apply new signal only once per new signal timestamp
                if sig_ts is not None and sig_ts != st.last_signal_ts_ms:
                    st.last_signal_ts_ms = sig_ts

                    if action == "FLAT":
                        target_spot = 0.0
                        target_perp = 0.0
                    elif action == "LONG_SPOT_SHORT_PERP":
                        target_spot = +size_btc
                        target_perp = -size_btc
                    else:
                        target_spot = 0.0
                        target_perp = 0.0

                    _apply_spot_trade(st, target_spot, spot_mid)
                    _apply_perp_trade(st, target_perp, perp_mark)
                    _save_state(st_path, st)

                eq = _equity(st, spot_mid, perp_mark)
                net_btc = st.q_spot_btc + st.q_perp_btc

                writer.writerow(
                    [
                        ts_ms,
                        symbol,
                        spot_mid,
                        perp_mark,
                        basis_bps,
                        funding_rate_8h,
                        minutes_to_funding,
                        action,
                        size_btc,
                        st.q_spot_btc,
                        st.q_perp_btc,
                        "" if st.perp_entry is None else st.perp_entry,
                        st.cash_usdt,
                        eq,
                        net_btc,
                        reason,
                    ]
                )
                f.flush()

                log.info(
                    "PAPER %s eq=%.2f cash=%.2f q_spot=%.4f q_perp=%.4f net=%.4f action=%s",
                    symbol,
                    eq,
                    st.cash_usdt,
                    st.q_spot_btc,
                    st.q_perp_btc,
                    net_btc,
                    action,
                )

                await asyncio.sleep(poll_s)

            except asyncio.CancelledError:
                log.info("paper_portfolio cancelled, shutting down cleanly.")
                return
            except KeyboardInterrupt:
                log.info("KeyboardInterrupt received, shutting down cleanly.")
                return
            except Exception as e:
                log.warning("paper_portfolio error: %s", repr(e))
                await asyncio.sleep(5.0)
