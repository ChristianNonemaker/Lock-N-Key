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
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from dk_ncaab.config.sports import (
    default_odds_sport_keys,
    default_schedule_sport_keys,
    validate_odds_sports,
    validate_schedule_sports,
)

_CONFIG_PATH = Path(__file__).parent / "settings.yaml"


# ── Sub-models ──────────────────────────────────────────────────
class DatabaseCfg(BaseModel):
    url: str = "sqlite:///artifacts/dk_ncaab.sqlite3"
    echo: bool = False


class OddsApiCfg(BaseModel):
    key: str = ""
    base_url: str = "https://api.the-odds-api.com/v4"
    # Backward-compatible single sport fallback.
    sport: str = "baseball_mlb"
    # Primary quota-safe odds target set. Schedule loading is configured
    # separately so we can load all free ESPN slates without spending odds quota.
    sports: list[str] = Field(default_factory=default_odds_sport_keys)
    monthly_request_budget: int = 500
    reserve_requests: int = 50
    max_sports_per_run: int = 1
    min_interval_minutes: int = 360
    max_request_attempts: int = 1
    bookmaker: str = "draftkings"
    regions: str = "us"
    markets: str = "h2h,spreads,totals"

    def active_sports(self) -> list[str]:
        """Return normalized active sports with legacy fallback behavior."""
        if sports := validate_odds_sports(self.sports):
            return sports
        fallback = self.sport.strip()
        if fallback:
            return validate_odds_sports([fallback])
        return default_odds_sport_keys()


class PollingCfg(BaseModel):
    odds_baseline_sec: int = 300
    odds_pre90_sec: int = 90
    odds_pre30_sec: int = 60
    splits_baseline_sec: int = 1800
    splits_pre90_sec: int = 600
    results_sec: int = 300


class ScheduleCfg(BaseModel):
    sports: list[str] = Field(default_factory=default_schedule_sport_keys)
    lookahead_days: int = 14
    request_delay_sec: float = 0.25

    def active_sports(self) -> list[str]:
        return validate_schedule_sports(self.sports)


class MlbStatsCfg(BaseModel):
    base_url: str = "https://statsapi.mlb.com/api/v1"
    max_boxscores_per_run: int = 50
    request_delay_sec: float = 0.1


class SplitsCfg(BaseModel):
    url: str = "https://www.actionnetwork.com/ncaab/public-betting"
    headless: bool = True
    timeout_ms: int = 30_000
    post_load_wait_ms: int = 2_000
    hard_timeout_sec: int = 45


class ApiCfg(BaseModel):
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:8501",
            "http://127.0.0.1:8501",
            "https://odds-vm.tail1282c7.ts.net",
        ]
    )


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
    schedule: ScheduleCfg = ScheduleCfg()
    mlb_stats: MlbStatsCfg = MlbStatsCfg()
    splits: SplitsCfg = SplitsCfg()
    api: ApiCfg = ApiCfg()
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
