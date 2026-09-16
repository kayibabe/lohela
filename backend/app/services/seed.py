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
]


async def seed_competitions_if_empty(db: AsyncSession) -> int:
    """Insert all competitions if the table is empty. Returns number added (0 if already seeded)."""
    count = (await db.execute(select(func.count()).select_from(Competition))).scalar()
    if count and count > 0:
        return 0
    for data in COMPETITIONS:
        db.add(Competition(**data, reliability_score=1.0, active=True))
    await db.commit()
    return len(COMPETITIONS)
