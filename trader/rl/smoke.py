# trader/rl/smoke.py
from __future__ import annotations

import argparse
import numpy as np

from trader.rl.env import RLCryptoEnv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--print_every", type=int, default=10)
    args = ap.parse_args()

    env = RLCryptoEnv()  # uses data/derived/spot_features_5m.csv by default
    obs, info = env.reset()

    print("obs_dim=", int(np.asarray(obs).shape[0]), "rows= NA")

    total_reward = 0.0
    last_eq = info.get("equity", 0.0)

    ACTION_NAME = {0: "FLAT", 1: "ENTER", 2: "HOLD"}

    for t in range(args.steps):
        a = int(env.action_space.sample())
        obs, r, terminated, truncated, info = env.step(a)

        total_reward += float(r)
        eff = int(info.get("effective", 0))
        pos = int(info.get("pos", 0))
        eq = float(info.get("equity", last_eq))
        unsafe = int(info.get("unsafe", 0))
        last_eq = eq

        if (args.print_every > 0) and (t % args.print_every == 0):
            an = ACTION_NAME.get(a, str(a))
            print(f"t={t:04d} a={a}({an}) eff={eff} pos={pos} r={r:.6f} eq={eq:.2f} unsafe={unsafe}")

        if terminated or truncated:
            break

    print(f"total_reward= {total_reward:.6f} final_equity= {last_eq:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
