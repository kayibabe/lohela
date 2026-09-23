"""One-time competition seed — run on first deploy when the table is empty."""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Competition, LeagueTier


COMPETITIONS = [
    # Tier 1
    {"api_football_id": 2,   "name": "UEFA Champions League",  "country": "World",       "tier": LeagueTier.TIER1, "home_advantage_elo": 65.0},
    {"api_football_id": 3,   "name": "UEFA Europa League",     "country": "World",       "tier": LeagueTier.TIER1, "home_advantage_elo": 65.0},
    {"api_football_id": 39,  "name": "Premier League",         "country": "England",     "tier": LeagueTier.TIER1, "home_advantage_elo": 70.0},
    {"api_football_id": 61,  "name": "Ligue 1",                "country": "France",      "tier": LeagueTier.TIER1, "home_advantage_elo": 75.0},
    {"api_football_id": 78,  "name": "Bundesliga",             "country": "Germany",     "tier": LeagueTier.TIER1, "home_advantage_elo": 80.0},
    {"api_football_id": 135, "name": "Serie A",                "country": "Italy",       "tier": LeagueTier.TIER1, "home_advantage_elo": 80.0},
    {"api_football_id": 140, "name": "La Liga",                "country": "Spain",       "tier": LeagueTier.TIER1, "home_advantage_elo": 75.0},
    # Tier 2
    {"api_football_id": 88,  "name": "Eredivisie",             "country": "Netherlands", "tier": LeagueTier.TIER2, "home_advantage_elo": 75.0},
    {"api_football_id": 94,  "name": "Primeira Liga",          "country": "Portugal",    "tier": LeagueTier.TIER2, "home_advantage_elo": 80.0},
    {"api_football_id": 40,  "name": "Championship",           "country": "England",     "tier": LeagueTier.TIER2, "home_advantage_elo": 70.0},
    {"api_football_id": 71,  "name": "Brasileirão Série A",   "country": "Brazil",      "tier": LeagueTier.TIER2, "home_advantage_elo": 85.0},
    {"api_football_id": 128, "name": "Argentine Primera",      "country": "Argentina",   "tier": LeagueTier.TIER2, "home_advantage_elo": 90.0},
    {"api_football_id": 62,  "name": "Ligue 2",                "country": "France",      "tier": LeagueTier.TIER2, "home_advantage_elo": 75.0},
    {"api_football_id": 307, "name": "Saudi Pro League",       "country": "Saudi Arabia","tier": LeagueTier.TIER2, "home_advantage_elo": 80.0},
    {"api_football_id": 357, "name": "League of Ireland",      "country": "Ireland",     "tier": LeagueTier.TIER2, "home_advantage_elo": 75.0},
    {"api_football_id": 72,  "name": "Brasileirão Série B",   "country": "Brazil",      "tier": LeagueTier.TIER2, "home_advantage_elo": 85.0},
    {"api_football_id": 79,  "name": "2. Bundesliga",          "country": "Germany",     "tier": LeagueTier.TIER2, "home_advantage_elo": 80.0},
    {"api_football_id": 106, "name": "Ekstraklasa",            "country": "Poland",      "tier": LeagueTier.TIER2, "home_advantage_elo": 80.0},
    {"api_football_id": 48,  "name": "League Cup",             "country": "England",     "tier": LeagueTier.TIER2, "home_advantage_elo": 65.0},
    {"api_football_id": 81,  "name": "DFB Pokal",              "country": "Germany",     "tier": LeagueTier.TIER2, "home_advantage_elo": 75.0},
    {"api_football_id": 137, "name": "Coppa Italia",           "country": "Italy",       "tier": LeagueTier.TIER2, "home_advantage_elo": 75.0},
    {"api_football_id": 179, "name": "Premiership",            "country": "Scotland",    "tier": LeagueTier.TIER2, "home_advantage_elo": 75.0},
    {"api_football_id": 144, "name": "Jupiler Pro League",     "country": "Belgium",     "tier": LeagueTier.TIER2, "home_advantage_elo": 75.0},
    {"api_football_id": 253, "name": "Major League Soccer",    "country": "USA",         "tier": LeagueTier.TIER2, "home_advantage_elo": 70.0},
    # Tier 3 — break-resilient supply (added 2026-09-23 after the Sept FIFA
    # window left 22-29 Sep with 0-5 tracked fixtures a day). Each keeps
    # playing through international windows and/or midweek, and each was
    # confirmed via GET /leagues to have odds coverage for the current season.
    # Unproven for this system, so seeded at reliability 0.8 rather than 1.0.
    {"api_football_id": 41,  "name": "League One",             "country": "England",     "tier": LeagueTier.TIER3, "home_advantage_elo": 70.0},
    {"api_football_id": 42,  "name": "League Two",             "country": "England",     "tier": LeagueTier.TIER3, "home_advantage_elo": 70.0},
    {"api_football_id": 43,  "name": "National League",        "country": "England",     "tier": LeagueTier.TIER3, "home_advantage_elo": 70.0},
    {"api_football_id": 180, "name": "Scottish Championship",  "country": "Scotland",    "tier": LeagueTier.TIER3, "home_advantage_elo": 75.0},
    {"api_football_id": 408, "name": "NIFL Premiership",       "country": "Northern Ireland", "tier": LeagueTier.TIER3, "home_advantage_elo": 75.0},
    {"api_football_id": 89,  "name": "Eerste Divisie",         "country": "Netherlands", "tier": LeagueTier.TIER3, "home_advantage_elo": 75.0},
    {"api_football_id": 141, "name": "Segunda División",       "country": "Spain",       "tier": LeagueTier.TIER3, "home_advantage_elo": 75.0},
    {"api_football_id": 435, "name": "Primera RFEF - Group 1", "country": "Spain",       "tier": LeagueTier.TIER3, "home_advantage_elo": 75.0},
    {"api_football_id": 436, "name": "Primera RFEF - Group 2", "country": "Spain",       "tier": LeagueTier.TIER3, "home_advantage_elo": 75.0},
    {"api_football_id": 138, "name": "Serie C - Girone A",     "country": "Italy",       "tier": LeagueTier.TIER3, "home_advantage_elo": 80.0},
    {"api_football_id": 942, "name": "Serie C - Girone B",     "country": "Italy",       "tier": LeagueTier.TIER3, "home_advantage_elo": 80.0},
    {"api_football_id": 943, "name": "Serie C - Girone C",     "country": "Italy",       "tier": LeagueTier.TIER3, "home_advantage_elo": 80.0},
    {"api_football_id": 239, "name": "Colombian Primera A",    "country": "Colombia",    "tier": LeagueTier.TIER3, "home_advantage_elo": 90.0},
    {"api_football_id": 262, "name": "Liga MX",                "country": "Mexico",      "tier": LeagueTier.TIER3, "home_advantage_elo": 85.0},
    {"api_football_id": 268, "name": "Uruguayan Primera",      "country": "Uruguay",     "tier": LeagueTier.TIER3, "home_advantage_elo": 85.0},
    {"api_football_id": 255, "name": "USL Championship",       "country": "USA",         "tier": LeagueTier.TIER3, "home_advantage_elo": 70.0},
]

_DEFAULT_RELIABILITY = {LeagueTier.TIER3: 0.8}


def _competition(data: dict) -> Competition:
    return Competition(
        **data,
        reliability_score=_DEFAULT_RELIABILITY.get(data["tier"], 1.0),
        active=True,
    )


async def seed_competitions_if_empty(db: AsyncSession) -> int:
    """Insert all competitions if the table is empty. Returns number added (0 if already seeded)."""
    count = (await db.execute(select(func.count()).select_from(Competition))).scalar()
    if count and count > 0:
        return 0
    for data in COMPETITIONS:
        db.add(_competition(data))
    await db.commit()
    return len(COMPETITIONS)


async def seed_missing_competitions(db: AsyncSession) -> list[int]:
    """Insert competitions from COMPETITIONS that have no row yet.

    Unlike seed_competitions_if_empty this reaches already-seeded databases,
    so adding a league to COMPETITIONS + config.tier2_league_ids is enough for
    production to start ingesting it on the next deploy. Existing rows —
    including any an operator deactivated or blacklisted — are never touched.
    Returns the api_football_ids added.
    """
    existing = set((await db.execute(select(Competition.api_football_id))).scalars().all())
    added = [data for data in COMPETITIONS if data["api_football_id"] not in existing]
    for data in added:
        db.add(_competition(data))
    if added:
        await db.commit()
    return [data["api_football_id"] for data in added]
