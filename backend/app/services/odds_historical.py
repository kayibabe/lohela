"""
Historical odds importer — Football-Data.co.uk CSV files.

Downloads free historical odds CSVs and matches them to our fixtures table
by home/away team name + date. Populates the odds table with closing odds
from multiple bookmakers for the 2024/25 season.

CSV URL format: https://www.football-data.co.uk/mmz4281/{season_code}/{league_code}.csv
e.g. https://www.football-data.co.uk/mmz4281/2425/E0.csv  → Premier League 2024/25

Bookmaker columns used (closing odds):
  B365H / B365D / B365A      — Bet365  1X2
  B365>2.5 / B365<2.5        — Bet365  Over/Under 2.5
  B365AHH / B365AHA          — Bet365  Asian Handicap (home/away)
  BWH / BWD / BWA            — Betway  1X2
  VCH / VCD / VCA            — VC Bet 1X2
  PSH / PSD / PSA            — Pinnacle 1X2 (sharpest market)
  P>2.5 / P<2.5              — Pinnacle Over/Under 2.5
"""

import asyncio
import csv
import io
import logging
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.config import settings
from app.models import Match, Odds, Competition, Team

logger = logging.getLogger(__name__)

# Map (api_football_id) → (season_code, league_code)
LEAGUE_CSV_MAP: dict[int, tuple[str, str]] = {
    39:  ("2425", "E0"),    # Premier League
    140: ("2425", "SP1"),   # La Liga
    135: ("2425", "I1"),    # Serie A
    78:  ("2425", "D1"),    # Bundesliga
    61:  ("2425", "F1"),    # Ligue 1
    # UCL/UEL not available on football-data.co.uk
}

# Bookmaker columns → bookmaker name
BOOKMAKER_1X2 = {
    "PSH": ("Pinnacle", "home_win"),
    "PSD": ("Pinnacle", "draw"),
    "PSA": ("Pinnacle", "away_win"),
    "B365H": ("Bet365", "home_win"),
    "B365D": ("Bet365", "draw"),
    "B365A": ("Bet365", "away_win"),
    "BWH": ("Betway", "home_win"),
    "BWD": ("Betway", "draw"),
    "BWA": ("Betway", "away_win"),
    "VCH": ("VC Bet", "home_win"),
    "VCD": ("VC Bet", "draw"),
    "VCA": ("VC Bet", "away_win"),
}

BOOKMAKER_TOTALS = {
    "P>2.5": ("Pinnacle", "over_2.5"),
    "P<2.5": ("Pinnacle", "under_2.5"),
    "B365>2.5": ("Bet365", "over_2.5"),
    "B365<2.5": ("Bet365", "under_2.5"),
}

# Only the rows this CSV importer owns may be replaced on re-import. Live
# API-sourced odds for the same match must survive, so the cleanup is scoped
# to these bookmakers rather than to the whole match.
CSV_BOOKMAKERS = sorted(
    {bookmaker for bookmaker, _ in BOOKMAKER_1X2.values()}
    | {bookmaker for bookmaker, _ in BOOKMAKER_TOTALS.values()}
)


class HistoricalOddsImporter:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def import_season(self, api_football_league_id: int) -> dict:
        """Download and import odds CSV for a league."""
        csv_info = LEAGUE_CSV_MAP.get(api_football_league_id)
        if not csv_info:
            return {"league_id": api_football_league_id, "status": "not_supported", "imported": 0}

        season_code, league_code = csv_info
        url = f"{settings.football_data_base_url}/{season_code}/{league_code}.csv"
        logger.info("Downloading odds CSV: %s", url)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            csv_content = resp.text

        rows = list(csv.DictReader(io.StringIO(csv_content)))
        logger.info("Parsed %d rows from %s", len(rows), url)

        # Load all matches for this competition
        comp_result = await self.db.execute(
            select(Competition).where(Competition.api_football_id == api_football_league_id)
        )
        competition = comp_result.scalar_one_or_none()
        if not competition:
            return {"league_id": api_football_league_id, "status": "competition_not_found", "imported": 0}

        matches_q = await self.db.execute(
            select(Match).where(Match.competition_id == competition.id)
        )
        all_matches = matches_q.scalars().all()
        teams_q = await self.db.execute(select(Team))
        all_teams = {t.id: t.name for t in teams_q.scalars().all()}

        # Build lookup: (date, home_name_lower, away_name_lower) → match
        match_lookup: dict[tuple, Match] = {}
        for m in all_matches:
            ko = m.kickoff_at.date() if m.kickoff_at else None
            if ko:
                home_name = all_teams.get(m.home_team_id, "").lower()
                away_name = all_teams.get(m.away_team_id, "").lower()
                match_lookup[(ko, home_name, away_name)] = m

        imported = 0
        unmatched = 0

        for row in rows:
            match_date = _parse_fd_date(row.get("Date", ""))
            if not match_date:
                continue

            home_raw = row.get("HomeTeam", "").strip()
            away_raw = row.get("AwayTeam", "").strip()
            if not home_raw or not away_raw:
                continue

            match = _find_match(match_lookup, match_date, home_raw, away_raw)
            if not match:
                unmatched += 1
                continue

            # Replace only this importer's own rows. Deleting every Odds row
            # for the match would discard fresher live API odds and replace
            # them with CSV fallbacks.
            await self.db.execute(
                delete(Odds).where(
                    Odds.match_id == match.id,
                    Odds.bookmaker.in_(CSV_BOOKMAKERS),
                )
            )

            # Insert 1X2 odds
            for col, (bookmaker, market) in BOOKMAKER_1X2.items():
                raw = row.get(col, "").strip()
                if not raw:
                    continue
                try:
                    dec_odds = float(raw)
                    if dec_odds <= 1.0:
                        continue
                    await _insert_odds(self.db, match.id, bookmaker, market, dec_odds)
                except ValueError:
                    pass

            # Insert Over/Under odds
            for col, (bookmaker, market) in BOOKMAKER_TOTALS.items():
                raw = row.get(col, "").strip()
                if not raw:
                    continue
                try:
                    dec_odds = float(raw)
                    if dec_odds <= 1.0:
                        continue
                    await _insert_odds(self.db, match.id, bookmaker, market, dec_odds)
                except ValueError:
                    pass

            await self.db.flush()
            imported += 1

        await self.db.commit()
        logger.info(
            "League %d: %d rows imported, %d unmatched",
            api_football_league_id, imported, unmatched,
        )
        return {
            "league_id": api_football_league_id,
            "status": "ok",
            "imported": imported,
            "unmatched": unmatched,
        }

    async def import_all(self) -> list[dict]:
        results = []
        for league_id in LEAGUE_CSV_MAP:
            result = await self.import_season(league_id)
            results.append(result)
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _insert_odds(
    db: AsyncSession,
    match_id: int,
    bookmaker: str,
    market: str,
    decimal_odds: float,
) -> None:
    obj = Odds(
        match_id=match_id,
        bookmaker=bookmaker,
        market=market,
        selection=market,  # selection = market key for simple markets
        decimal_odds=decimal_odds,
        implied_probability=round(1.0 / decimal_odds, 6),
        source_type="historical_football_data",
        is_fallback=True,
    )
    db.add(obj)


def _parse_fd_date(date_str: str) -> Optional[date]:
    """Parse Football-Data.co.uk date — either DD/MM/YY or DD/MM/YYYY."""
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# Known name mappings between Football-Data.co.uk and API-Football team names
_NAME_ALIASES: dict[str, str] = {
    "man united": "manchester united",
    "man utd": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "sheffield utd": "sheffield united",
    "sheff utd": "sheffield united",
    "nott'm forest": "nottingham forest",
    "newcastle": "newcastle united",
    "brighton": "brighton & hove albion",
    "west ham": "west ham united",
    "luton": "luton town",
    "brentford": "brentford",
    "bournemouth": "afc bournemouth",
    "athletico madrid": "atletico madrid",
    "atletico madrid": "atletico madrid",
    "real madrid": "real madrid",
    "barcelona": "fc barcelona",
    "betis": "real betis",
    "sociedad": "real sociedad",
    "alaves": "deportivo alaves",
    "celta vigo": "celta vigo",
    "las palmas": "ud las palmas",
    "st etienne": "saint-etienne",
    "paris sg": "paris saint-germain",
    "psg": "paris saint-germain",
    "m'gladbach": "borussia monchengladbach",
    "ein frankfurt": "eintracht frankfurt",
    "b. dortmund": "borussia dortmund",
    "rb leipzig": "rasenballsport leipzig",
    "hertha": "hertha bsc",
    "leverkusen": "bayer leverkusen",
    "augsburg": "fc augsburg",
    "mainz": "1. fsv mainz 05",
    "mainz 05": "1. fsv mainz 05",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "ac milan": "ac milan",
    "juventus": "juventus",
    "napoli": "ssc napoli",
    "lazio": "ss lazio",
    "roma": "as roma",
    "fiorentina": "acf fiorentina",
    "atalanta": "atalanta bc",
    "torino": "torino fc",
    "udinese": "udinese calcio",
    "genoa": "genoa cfc",
    "verona": "hellas verona",
    "lecce": "us lecce",
    "cagliari": "cagliari calcio",
    "empoli": "empoli fc",
    "venezia": "venezia fc",
    "monza": "ac monza",
    "como": "como 1907",
}


def _normalise(name: str) -> str:
    n = name.lower().strip()
    return _NAME_ALIASES.get(n, n)


def _find_match(
    lookup: dict[tuple, Match],
    match_date: date,
    home_raw: str,
    away_raw: str,
) -> Optional[Match]:
    home_norm = _normalise(home_raw)
    away_norm = _normalise(away_raw)

    # Exact match
    key = (match_date, home_norm, away_norm)
    if key in lookup:
        return lookup[key]

    # ±1 day window (timezone edge cases)
    from datetime import timedelta
    for delta in (-1, 1):
        adj_date = match_date + timedelta(days=delta)
        key_adj = (adj_date, home_norm, away_norm)
        if key_adj in lookup:
            return lookup[key_adj]

    # Fuzzy name match on same date (handles minor naming differences)
    best_score = 0.0
    best_match = None
    from datetime import timedelta
    for delta in (-1, 0, 1):
        adj_date = match_date + timedelta(days=delta)
        for (d, h, a), m in lookup.items():
            if d != adj_date:
                continue
            score = (_name_similarity(home_norm, h) + _name_similarity(away_norm, a)) / 2
            if score > best_score:
                best_score = score
                best_match = m

    if best_score >= 0.75:
        return best_match

    return None
