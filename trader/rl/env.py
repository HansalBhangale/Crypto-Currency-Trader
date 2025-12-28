# trader/rl/env.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    import gymnasium as gym
except Exception:  # fallback if you used gym
    import gym  # type: ignore

log = logging.getLogger("trader.rl.env")

DEFAULT_FEATURES_CSV = "data/derived/spot_features_5m.csv"


def load_default_df(csv_path: str = DEFAULT_FEATURES_CSV) -> pd.DataFrame:
    """
    Loads the default dataset used by RL env (derived spot features).
    """
    p = Path(csv_path)
    if not p.exists():
        raise FileNotFoundError(
            f"RL features CSV not found: {p}. "
            f"Run: python -m trader.main --config config/config.yaml --run spot_features_5m"
        )
    df = pd.read_csv(p)
    if df.empty:
        raise ValueError(f"RL features CSV is empty: {p}")
    return df


@dataclass
class EnvConfig:
    initial_equity: float = 10_000.0
    # action meanings: 0=FLAT, 1=LONG_SPOT_SHORT_PERP, 2=HOLD
    n_actions: int = 3


class RLCryptoEnv(gym.Env):
    """
    Minimal RL environment over your derived feature CSV.

    Observation (7 dims by default):
      [basis_bps, funding_rate_8h, minutes_to_funding, r_5m, vol_1h, spread_bps_mean, unsafe]

    Action space:
      0 = FLAT
      1 = LONG_SPOT_SHORT_PERP  (enter/ensure position=1)
      2 = HOLD                 (keep current position)
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        df: pd.DataFrame | None = None,
        csv_path: str = DEFAULT_FEATURES_CSV,
        cfg: EnvConfig | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg or EnvConfig()
        if df is None:
            df = load_default_df(csv_path)

        self.df = df.reset_index(drop=True).copy()

        # ---- ensure required columns exist (use safe defaults) ----
        for col, default in [
            ("basis_bps", 0.0),
            ("funding_rate_8h", 0.0),
            ("minutes_to_funding", 9999.0),
            ("r_5m", 0.0),
            ("vol_1h", 0.0),
            ("spread_bps_mean", 0.0),
            ("unsafe", 0),
        ]:
            if col not in self.df.columns:
                self.df[col] = default

        # NaN safety
        self.df["r_5m"] = pd.to_numeric(self.df["r_5m"], errors="coerce").fillna(0.0)
        self.df["vol_1h"] = pd.to_numeric(self.df["vol_1h"], errors="coerce").fillna(0.0)
        self.df["spread_bps_mean"] = pd.to_numeric(self.df["spread_bps_mean"], errors="coerce").fillna(0.0)
        self.df["basis_bps"] = pd.to_numeric(self.df["basis_bps"], errors="coerce").fillna(0.0)
        self.df["funding_rate_8h"] = pd.to_numeric(self.df["funding_rate_8h"], errors="coerce").fillna(0.0)
        self.df["minutes_to_funding"] = pd.to_numeric(self.df["minutes_to_funding"], errors="coerce").fillna(9999.0)
        self.df["unsafe"] = pd.to_numeric(self.df["unsafe"], errors="coerce").fillna(1).astype(int)

        self.n_rows = len(self.df)
        if self.n_rows < 2:
            raise ValueError(f"Need at least 2 rows for env, got {self.n_rows}")

        # Gym spaces
        self.action_space = gym.spaces.Discrete(self.cfg.n_actions)
        self.observation_space = gym.spaces.Box(
            low=-np.inf, high=np.inf, shape=(7,), dtype=np.float32
        )

        # episode state
        self.t: int = 0
        self.pos: int = 0  # 0 flat, 1 in position
        self.equity: float = float(self.cfg.initial_equity)

    def _obs_from_row(self, i: int) -> np.ndarray:
        r = self.df.iloc[i]
        obs = np.array(
            [
                float(r["basis_bps"]),
                float(r["funding_rate_8h"]),
                float(r["minutes_to_funding"]),
                float(r["r_5m"]),
                float(r["vol_1h"]),
                float(r["spread_bps_mean"]),
                float(r["unsafe"]),
            ],
            dtype=np.float32,
        )
        return obs

    def reset(self, *, seed: Optional[int] = None, options=None):
        super().reset(seed=seed)
        self.t = 0
        self.pos = 0
        self.equity = float(self.cfg.initial_equity)
        obs = self._obs_from_row(self.t)
        info = {"t": self.t, "pos": self.pos, "equity": self.equity, "unsafe": int(obs[-1])}
        return obs, info

    def step(self, action: int):
        action = int(action)
        action = max(0, min(action, self.cfg.n_actions - 1))

        # current row (unsafe gate)
        r = self.df.iloc[self.t]
        unsafe = int(r["unsafe"])

        prev_pos = int(self.pos)

        # Action semantics:
        # 0 = FLAT (target pos 0)
        # 1 = ENTER (target pos 1)
        # 2 = HOLD (keep pos)
        if unsafe == 1:
            target_pos = 0
        else:
            if action == 0:
                target_pos = 0
            elif action == 1:
                target_pos = 1
            else:  # action == 2
                target_pos = prev_pos

        # effective = position actually changed (trade event)
        self.pos = int(target_pos)
        effective = 1 if self.pos != prev_pos else 0

        # move forward one step
        t_next = min(self.t + 1, self.n_rows - 1)

        # reward: simple PnL proxy
        r_next = self.df.iloc[t_next]["r_5m"]
        r_next = float(r_next) if (r_next == r_next) else 0.0  # NaN-safe

        reward = (r_next * 10_000.0) if self.pos == 1 else 0.0  # dollars-ish
        if unsafe == 1:
            reward -= 0.5  # small penalty for unsafe rows

        # update equity
        self.equity = float(self.equity) + float(reward)

        self.t = t_next
        terminated = (self.t >= self.n_rows - 1)
        truncated = False

        obs = self._obs_from_row(self.t)
        info = {
            "t": int(self.t),
            "pos": int(self.pos),
            "effective": int(effective),
            "equity": float(self.equity),
            "unsafe": int(obs[-1]),
        }
        return obs, float(reward), terminated, truncated, info
