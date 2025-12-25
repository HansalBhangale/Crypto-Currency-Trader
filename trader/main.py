from __future__ import annotations

import argparse
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
