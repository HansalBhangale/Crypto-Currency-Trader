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
    last_equity_usdt: Optional[float] = None


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
            last_equity_usdt=d.get("last_equity_usdt", None),
        )
        if st.perp_entry is not None:
            st.perp_entry = float(st.perp_entry)
        if st.last_equity_usdt is not None:
            st.last_equity_usdt = float(st.last_equity_usdt)
        return st
    except Exception as e:
        log.warning("Failed loading state %s: %s (starting fresh)", path, repr(e))
        return PaperState(cash_usdt=float(initial_cash))


def _save_state(path: Path, st: PaperState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(st), indent=2), encoding="utf-8")


def _equity(st: PaperState, spot_mid: float, perp_mark: float) -> float:
    spot_val = st.q_spot_btc * spot_mid
    perp_unreal = 0.0
    if st.perp_entry is not None and abs(st.q_perp_btc) > 1e-12:
        perp_unreal = st.q_perp_btc * (perp_mark - st.perp_entry)
    return st.cash_usdt + spot_val + perp_unreal


def _apply_spot_trade(st: PaperState, target_q_spot: float, spot_mid: float) -> float:
    """
    Apply spot delta at mid. Returns dq_spot (BTC).
    """
    dq = target_q_spot - st.q_spot_btc
    if abs(dq) < 1e-12:
        return 0.0
    st.cash_usdt -= dq * spot_mid  # buy reduces cash, sell increases cash
    st.q_spot_btc = target_q_spot
    return dq


def _apply_perp_trade(st: PaperState, target_q_perp: float, perp_mark: float) -> float:
    """
    Apply perp target by realizing pnl on closed qty and updating entry.
    Returns dq_perp (BTC).
    """
    q0 = st.q_perp_btc
    dq = target_q_perp - q0
    if abs(dq) < 1e-12:
        return 0.0

    def realize_pnl(closed_qty: float) -> None:
        if st.perp_entry is None:
            return
        pnl = closed_qty * (perp_mark - st.perp_entry)
        st.cash_usdt += pnl

    # flat -> open
    if abs(q0) < 1e-12:
        st.q_perp_btc = target_q_perp
        st.perp_entry = perp_mark if abs(target_q_perp) > 1e-12 else None
        return dq

    # close all
    if abs(target_q_perp) < 1e-12:
        realize_pnl(q0)
        st.q_perp_btc = 0.0
        st.perp_entry = None
        return dq

    same_sign = (q0 > 0 and target_q_perp > 0) or (q0 < 0 and target_q_perp < 0)

    if same_sign:
        # increase -> update avg entry
        if abs(target_q_perp) > abs(q0):
            add_qty = target_q_perp - q0
            st.perp_entry = (q0 * st.perp_entry + add_qty * perp_mark) / target_q_perp  # type: ignore[operator]
            st.q_perp_btc = target_q_perp
            return dq

        # reduce -> realize on reduced
        if abs(target_q_perp) < abs(q0):
            closed_qty = q0 - target_q_perp
            realize_pnl(closed_qty)
            st.q_perp_btc = target_q_perp
            if abs(st.q_perp_btc) < 1e-12:
                st.q_perp_btc = 0.0
                st.perp_entry = None
            return dq

    # flip sign -> close then open
    realize_pnl(q0)
    st.q_perp_btc = target_q_perp
    st.perp_entry = perp_mark
    return dq


def _rotate_if_old_header(out_path: Path, required_cols: list[str]) -> None:
    """
    If an existing CSV has an old header (missing required columns), rename it to a .bak file.
    """
    if not out_path.exists():
        return
    try:
        with out_path.open("r", encoding="utf-8") as f:
            header = f.readline().strip()
        if not header:
            return
        cols = header.split(",")
        missing = [c for c in required_cols if c not in cols]
        if missing:
            ts = int(time.time())
            bak = out_path.with_suffix(f".bak.{ts}.csv")
            out_path.rename(bak)
            log.warning("Rotated old paper portfolio CSV -> %s (missing cols: %s)", bak, missing)
    except Exception as e:
        log.warning("Could not rotate old CSV %s: %s", out_path, repr(e))


async def run_paper_portfolio(
    baseline_signals_csv: str,
    basis_1m_csv: str,
    out_csv: str,
    state_json: str,
    initial_cash_usdt: float = 10_000.0,
    poll_s: float = 60.0,
    # costs (bps)
    spot_fee_bps: float = 10.0,
    perp_fee_bps: float = 4.0,
    slip_bps: float = 2.0,
) -> None:
    sig_path = Path(baseline_signals_csv)
    basis_path = Path(basis_1m_csv)
    out_path = Path(out_csv)
    st_path = Path(state_json)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    st = _load_state(st_path, initial_cash_usdt)

    required_cols = [
        "ts_ms", "symbol",
        "spot_mid", "perp_mark", "basis_bps", "funding_rate_8h", "minutes_to_funding",
        "signal_action", "signal_size_btc", "reason",
        "dq_spot_btc", "dq_perp_btc",
        "fees_usdt", "slippage_usdt", "funding_usdt",
        "q_spot_btc", "q_perp_btc", "perp_entry",
        "cash_usdt",
        "equity_before_usdt", "equity_after_usdt", "pnl_step_usdt",
        "net_btc",
    ]

    _rotate_if_old_header(out_path, required_cols)

    file_exists = out_path.exists()
    with out_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(required_cols)

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

                # equity BEFORE applying this tick's cashflows
                equity_before = _equity(st, spot_mid, perp_mark)

                # --- Funding cashflow every tick (perp only) ---
                # funding_rate_8h applies to notional q_perp * price over 8 hours
                # per minute: / 480 ; scale by poll minutes
                poll_minutes = poll_s / 60.0
                funding_usdt = -(st.q_perp_btc * perp_mark * funding_rate_8h * (poll_minutes / 480.0))
                # cash increases when funding_usdt > 0 (typically short when funding positive)
                st.cash_usdt += funding_usdt

                fees_usdt = 0.0
                slippage_usdt = 0.0
                dq_spot = 0.0
                dq_perp = 0.0

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

                    # Trades (instant fills at mid/mark for now)
                    dq_spot = _apply_spot_trade(st, target_spot, spot_mid)
                    dq_perp = _apply_perp_trade(st, target_perp, perp_mark)

                    # --- Fees (taker baseline) ---
                    fees_usdt += abs(dq_spot) * spot_mid * (spot_fee_bps / 10_000.0)
                    fees_usdt += abs(dq_perp) * perp_mark * (perp_fee_bps / 10_000.0)
                    st.cash_usdt -= fees_usdt

                    # --- Slippage (simple bps model, always a cost) ---
                    slippage_usdt += abs(dq_spot) * spot_mid * (slip_bps / 10_000.0)
                    slippage_usdt += abs(dq_perp) * perp_mark * (slip_bps / 10_000.0)
                    st.cash_usdt -= slippage_usdt

                    _save_state(st_path, st)

                equity_after = _equity(st, spot_mid, perp_mark)

                # Step PnL = change in equity from last tick (persisted), else 0 on first tick
                if st.last_equity_usdt is None:
                    pnl_step = 0.0
                else:
                    pnl_step = equity_after - st.last_equity_usdt

                st.last_equity_usdt = equity_after
                _save_state(st_path, st)

                net_btc = st.q_spot_btc + st.q_perp_btc

                writer.writerow(
                    [
                        ts_ms, symbol,
                        spot_mid, perp_mark, basis_bps, funding_rate_8h, minutes_to_funding,
                        action, size_btc, reason,
                        dq_spot, dq_perp,
                        fees_usdt, slippage_usdt, funding_usdt,
                        st.q_spot_btc, st.q_perp_btc, "" if st.perp_entry is None else st.perp_entry,
                        st.cash_usdt,
                        equity_before, equity_after, pnl_step,
                        net_btc,
                    ]
                )
                f.flush()

                log.info(
                    "PAPER %s eq=%.2f pnl_step=%.2f fees=%.4f slip=%.4f fund=%.4f q_spot=%.4f q_perp=%.4f action=%s",
                    symbol,
                    equity_after,
                    pnl_step,
                    fees_usdt,
                    slippage_usdt,
                    funding_usdt,
                    st.q_spot_btc,
                    st.q_perp_btc,
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
