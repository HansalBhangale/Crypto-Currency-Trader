from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from trader.logging_setup import setup_logging
from trader.settings import Settings

log = logging.getLogger("trader.main")


def ensure_dirs(settings: Settings) -> None:
    Path(settings.paths.data_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.paths.raw_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.paths.derived_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.paths.logs_dir).mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="RL Crypto Trader")
    parser.add_argument("--config", type=str, required=True, help="Path to config YAML")
    parser.add_argument(
        "--run",
        type=str,
        default="",
        choices=["", "spot_bbo", "spot_bars_1m", "spot_bars_5m", "spot_features_5m", "perp_funding"],

        help="Optional runnable: spot_bbo",
    )
    args = parser.parse_args()

    settings = Settings.load(args.config)
    setup_logging(
        level=settings.logging.level,
        to_file=settings.logging.to_file,
        filename=settings.logging.filename,
    )
    ensure_dirs(settings)

    log.info("OK: config + logging initialized")
    log.info("Project=%s Env=%s Exchange=%s", settings.project.name, settings.project.env, settings.project.exchange)
    log.info("Spot symbol=%s Perp symbol=%s", settings.project.symbol_spot, settings.project.symbol_perp)
    log.info("Dirs: raw=%s derived=%s logs=%s", settings.paths.raw_dir, settings.paths.derived_dir, settings.paths.logs_dir)

    if args.run == "spot_bbo":
        from trader.data.binance_spot_bbo import stream_spot_bbo

        out_csv = str(Path(settings.paths.raw_dir) / "spot_bbo.csv")
        log.info("Starting spot BBO stream -> %s", out_csv)
        asyncio.run(stream_spot_bbo(settings.project.symbol_spot, out_csv))
        return 0
    if args.run == "spot_bars_1m":
        from trader.features.spot_bars import build_spot_1m_bars

        in_csv = str(Path(settings.paths.raw_dir) / "spot_bbo.csv")
        out_csv = str(Path(settings.paths.derived_dir) / "spot_1m_bars.csv")
        log.info("Building 1m bars: %s -> %s", in_csv, out_csv)
        build_spot_1m_bars(in_csv, out_csv)
        return 0
    if args.run == "spot_bars_5m":
        from trader.features.spot_bars import build_spot_5m_bars

        in_csv = str(Path(settings.paths.derived_dir) / "spot_1m_bars.csv")
        out_csv = str(Path(settings.paths.derived_dir) / "spot_5m_bars.csv")
        log.info("Building 5m bars: %s -> %s", in_csv, out_csv)
        build_spot_5m_bars(in_csv, out_csv)
        return 0
    if args.run == "spot_features_5m":
        from trader.features.spot_features import build_spot_features_5m

        in_csv = str(Path(settings.paths.derived_dir) / "spot_5m_bars.csv")
        out_csv = str(Path(settings.paths.derived_dir) / "spot_features_5m.csv")
        log.info("Building 5m features: %s -> %s", in_csv, out_csv)
        build_spot_features_5m(in_csv, out_csv)
        return 0
    if args.run == "perp_funding":
        from trader.data.binance_perp_funding import stream_perp_funding

        out_csv = str(Path(settings.paths.raw_dir) / "perp_funding.csv")
        log.info("Starting perp funding poller -> %s", out_csv)
        asyncio.run(stream_perp_funding(settings.project.symbol_perp, out_csv, poll_s=60.0))
        return 0




    return 0


if __name__ == "__main__":
    raise SystemExit(main())
