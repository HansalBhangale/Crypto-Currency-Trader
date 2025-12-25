from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    env: str
    exchange: str
    symbol_spot: str
    symbol_perp: str


@dataclass(frozen=True)
class PathConfig:
    data_dir: str
    raw_dir: str
    derived_dir: str
    logs_dir: str


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    to_file: bool
    filename: str


@dataclass(frozen=True)
class Settings:
    project: ProjectConfig
    paths: PathConfig
    logging: LoggingConfig

    @staticmethod
    def load(config_path: str | Path) -> "Settings":
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")

        with config_path.open("r", encoding="utf-8") as f:
            raw: Dict[str, Any] = yaml.safe_load(f) or {}

        # Validate expected sections (minimal, strict enough for v0)
        for key in ("project", "paths", "logging"):
            if key not in raw:
                raise ValueError(f"Missing '{key}' section in config: {config_path}")

        p = raw["project"]
        paths = raw["paths"]
        log = raw["logging"]

        project_cfg = ProjectConfig(
            name=str(p["name"]),
            env=str(p["env"]),
            exchange=str(p["exchange"]),
            symbol_spot=str(p["symbol_spot"]),
            symbol_perp=str(p["symbol_perp"]),
        )
        path_cfg = PathConfig(
            data_dir=str(paths["data_dir"]),
            raw_dir=str(paths["raw_dir"]),
            derived_dir=str(paths["derived_dir"]),
            logs_dir=str(paths["logs_dir"]),
        )
        logging_cfg = LoggingConfig(
            level=str(log["level"]),
            to_file=bool(log["to_file"]),
            filename=str(log["filename"]),
        )

        return Settings(project=project_cfg, paths=path_cfg, logging=logging_cfg)
