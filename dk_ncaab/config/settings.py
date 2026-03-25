"""
Typed configuration loaded from settings.yaml with env-var overrides.

Env vars use prefix DKNCAAB_ and double-underscore nesting:
    DKNCAAB_DATABASE__URL=postgresql+psycopg2://...
    DKNCAAB_ODDS_API__KEY=abc123
"""

from __future__ import annotations

from pathlib import Path
from functools import lru_cache

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings

_CONFIG_PATH = Path(__file__).parent / "settings.yaml"


# ── Sub-models ──────────────────────────────────────────────────
class DatabaseCfg(BaseModel):
    url: str = "postgresql+psycopg2://dk:dk@localhost:5432/dk_ncaab"
    echo: bool = False


class OddsApiCfg(BaseModel):
    key: str = ""
    base_url: str = "https://api.the-odds-api.com/v4"
    sport: str = "basketball_ncaab"
    bookmaker: str = "draftkings"
    regions: str = "us"
    markets: str = "h2h,spreads,totals"


class PollingCfg(BaseModel):
    odds_baseline_sec: int = 300
    odds_pre90_sec: int = 90
    odds_pre30_sec: int = 60
    splits_baseline_sec: int = 1800
    splits_pre90_sec: int = 600
    results_sec: int = 300


class SplitsCfg(BaseModel):
    url: str = "https://www.actionnetwork.com/ncaab/public-betting"
    headless: bool = True
    timeout_ms: int = 30_000


class StorageCfg(BaseModel):
    raw_html_dir: str = "artifacts/raw_html"
    screenshots_dir: str = "artifacts/screenshots"
    parquet_dir: str = "artifacts/parquet"


class MatchingCfg(BaseModel):
    time_tolerance_min: int = 15


# ── Root settings ───────────────────────────────────────────────
class Settings(BaseSettings):
    database: DatabaseCfg = DatabaseCfg()
    odds_api: OddsApiCfg = OddsApiCfg()
    polling: PollingCfg = PollingCfg()
    splits: SplitsCfg = SplitsCfg()
    storage: StorageCfg = StorageCfg()
    matching: MatchingCfg = MatchingCfg()

    model_config = {
        "env_prefix": "DKNCAAB_",
        "env_nested_delimiter": "__",
        "env_file": str(Path(__file__).resolve().parents[2] / ".env"),
        "env_file_encoding": "utf-8",
    }


def _load_yaml() -> dict:
    """Read settings.yaml and return as dict."""
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings: YAML values overridden by env vars."""
    return Settings(**_load_yaml())
