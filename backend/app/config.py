from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator
from typing import Literal
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


CAT = ZoneInfo("Africa/Blantyre")

# Single source of truth for the prediction model release used by automated
# runs and API defaults. Historical rows retain the version they were created
# with, so changing this value starts a new auditable model lineage.
CURRENT_MODEL_VERSION = "0.2.1"


def cat_today():
    """Return today's date in the product's Malawi/CAT timezone."""
    return datetime.now(CAT).date()


def cat_day_bounds_utc(target_date: date) -> tuple[datetime, datetime]:
    """Return the UTC interval for one complete product day in CAT."""
    start = datetime.combine(target_date, time.min, tzinfo=CAT).astimezone(timezone.utc)
    end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=CAT).astimezone(timezone.utc)
    return start, end


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # API-Football
    api_football_key: str = ""
    rapidapi_key: str = ""
    api_football_host: Literal["direct", "rapidapi"] = "direct"

    @field_validator("api_football_key", "rapidapi_key", mode="before")
    @classmethod
    def strip_bom(cls, v: str) -> str:
        return v.lstrip("﻿").strip() if isinstance(v, str) else v

    # Database
    database_url: str = "postgresql+asyncpg://lohela:lohela_pass@localhost:5432/lohela"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        # Railway (and Heroku/Render) provide postgresql:// or postgres://;
        # asyncpg requires the postgresql+asyncpg:// scheme.
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return "postgresql+asyncpg://" + v[len("postgres://"):]
            if v.startswith("postgresql://"):
                return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # App
    app_env: str = "development"
    secret_key: str = "change_me_in_production"
    research_api_key: str = ""
    log_level: str = "INFO"

    # Pipeline windows: 00:15 CAT (22:15 UTC previous day) and 05:00 CAT (03:00 UTC).
    pipeline_early_cron_hour: int = 22
    pipeline_early_cron_minute: int = 15
    pipeline_morning_cron_hour: int = 3
    pipeline_morning_cron_minute: int = 0

    # Reliability guardrails. The watchdog checks that the latest due daily
    # window has a real full-pipeline attempt and queues one catch-up when it
    # does not. Stale attempts may be retried, but retries are bounded so a
    # provider outage cannot create an infinite API loop.
    pipeline_startup_catchup_enabled: bool = True
    pipeline_watchdog_interval_minutes: int = 5
    pipeline_stale_after_minutes: int = 45
    pipeline_automatic_retry_limit: int = 2

    # Finished results are reconciled immediately on startup and periodically
    # while the app is running. Startup uses the wider window after downtime.
    settlement_interval_minutes: int = 10
    settlement_lookback_days: int = 7
    settlement_startup_lookback_days: int = 30
    scheduler_leader_lock_enabled: bool = True
    scheduler_leader_lock_name: str = "lohela:apscheduler:leader"

    # Weekly offline learning creates shadow challengers only. Promotion remains manual.
    learning_shadow_schedule_enabled: bool = True
    learning_shadow_cron_day: str = "sun"
    learning_shadow_cron_hour: int = 4
    learning_shadow_cron_minute: int = 30
    learning_train_days: int = 180
    learning_validation_days: int = 30

    # The automated twice-daily pipeline updates the "Bayesian" leg's team
    # posteriors via ModelPreparationService. "analytical" is a closed-form
    # MAP estimate (goals scored/conceded relative to league average) — fast
    # and fully reproducible, which matters for the backtester's no-lookahead
    # guarantees. "advi" runs real PyMC variational inference and is slower
    # and non-deterministic run-to-run; opt in only if that trade-off is
    # wanted for the automated schedule. /models/run-bayesian always supports
    # both methods on demand regardless of this default.
    bayesian_estimation_method: Literal["analytical", "advi"] = "analytical"

    # Data quality thresholds (spec §26, §7)
    min_data_quality_score: int = 40     # below this: excluded from models
    warn_data_quality_score: int = 60    # below this: flagged
    max_selection_odds_age_hours: float = 2.0
    min_selection_edge: float = 0.03

    # On thin weekday slates the full-strength tier gates (esp. high-grade-leg
    # ratio) can legitimately admit zero combinations even with a healthy
    # qualified pool. AccumulatorBuilder relaxes the tightest gates for missing
    # public tiers, in bounded steps, until this floor is met or the relaxation
    # ladder is exhausted — see accumulator_builder._RELAXATION_STEPS.
    ticket_relaxation_enabled: bool = True
    min_daily_public_tickets: int = 2

    # Tier 1 league IDs on API-Football (spec Appendix B)
    tier1_league_ids: list[int] = [2, 3, 39, 61, 78, 135, 140]
    # Approved Tier 2 research leagues. These expand the candidate pool while
    # retaining the optimiser's per-league concentration limits.
    tier2_league_ids: list[int] = [
        88, 94, 40, 71, 128,
        # Bookmaker-covered leagues with useful same-day fixture depth.
        62, 307, 357, 72, 79, 106,
        # Weekday-fixture depth (added after the 2026-08-30..09-03 zero-ticket
        # outage — weekday slates from the Tier 1/2 leagues above were too thin
        # to clear the tier gates). All confirmed with live odds coverage for
        # the current season via GET /leagues before adding:
        # League Cup (England), DFB-Pokal (Germany), Coppa Italia — domestic
        # cups from already-covered countries, so teams have existing history;
        # cup rounds are scheduled midweek precisely when league play doesn't
        # fill the slate. Plus three more leagues to round out coverage.
        48, 81, 137,
        179, 144, 253,
    ]

    # Football-Data.co.uk (historical odds CSVs — no key required)
    football_data_base_url: str = "https://www.football-data.co.uk/mmz4281"

    # Cache TTLs (seconds)
    cache_ttl_fixtures: int = 86400   # 24 h — retain pulled fixture lists for reference
    cache_ttl_odds: int = 1800        # 30 min — API-Football odds drift, but not per-second
    cache_ttl_stats: int = 86400      # 24 h — per-fixture stats/injuries (historical)

    @model_validator(mode="after")
    def reject_insecure_production_defaults(self):
        if self.app_env == "production":
            if self.secret_key == "change_me_in_production" or len(self.secret_key) < 32:
                raise ValueError("Production SECRET_KEY must be a unique value of at least 32 characters")
            if len(self.research_api_key) < 32:
                raise ValueError("Production RESEARCH_API_KEY must be at least 32 characters")
        return self

    @property
    def api_football_base_url(self) -> str:
        if self.api_football_host == "rapidapi":
            return "https://api-football-v1.p.rapidapi.com/v3"
        return "https://v3.football.api-sports.io"

    @property
    def api_football_headers(self) -> dict:
        if self.api_football_host == "rapidapi":
            return {
                "X-RapidAPI-Key": self.rapidapi_key,
                "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com",
            }
        return {"x-apisports-key": self.api_football_key}


settings = Settings()
