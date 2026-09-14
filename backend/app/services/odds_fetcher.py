"""Bookmaker odds ingestion from API-Football v3 /odds."""
import logging
import re
from datetime import date, datetime, timezone
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import cat_day_bounds_utc, cat_today, settings
from app.models import Match, MatchStatus, Odds, OddsSnapshot
from app.services.fixture_ingestor import APIFootballClient

logger = logging.getLogger(__name__)
BET_IDS = {1, 5, 8, 12}

class LiveOddsFetcher:
    def __init__(self, db: AsyncSession): self.db = db

    async def fetch_all(self, target_date: date | None = None) -> dict:
        key = settings.api_football_key if settings.api_football_host == "direct" else settings.rapidapi_key
        if not key: return {"status": "skipped", "reason": "no_api_football_key"}
        target = target_date or cat_today()
        start, end = cat_day_bounds_utc(target)
        start = max(start, datetime.now(timezone.utc))
        result = await self.db.execute(select(Match).where(
            Match.kickoff_at >= start,
            Match.kickoff_at < end,
            Match.status == MatchStatus.SCHEDULED,
        ))
        matches = result.scalars().all(); inserted = 0; errors = []
        async with APIFootballClient() as client:
            for match in matches:
                try: inserted += await self._fetch_fixture(client, match)
                except Exception as exc:
                    logger.error("API-Football odds failed for %s: %s", match.api_football_id, exc)
                    errors.append({"fixture": match.api_football_id, "error": str(exc)})
        await self.db.commit()
        return {"status": "partial" if errors else "ok", "inserted": inserted, "fixtures": len(matches), "errors": errors}

    async def _fetch_fixture(self, client, match: Match) -> int:
        data = await client.get("/odds", {"fixture": match.api_football_id}, "odds", 1800)
        rows = data.get("response", [])
        if not rows: return 0
        result = await self.db.execute(select(Odds).where(Odds.match_id == match.id))
        opening = {(o.bookmaker, o.market, o.selection): o.opening_odds if o.opening_odds is not None else o.decimal_odds for o in result.scalars().all()}
        previous_result = await self.db.execute(select(OddsSnapshot).where(OddsSnapshot.match_id == match.id).order_by(OddsSnapshot.captured_at.desc()))
        previous = {(o.bookmaker, o.market, o.selection): o.decimal_odds for o in previous_result.scalars().all()}
        captured_at = datetime.now(timezone.utc)
        async with self.db.begin_nested():
            await self.db.execute(delete(Odds).where(Odds.match_id == match.id)); await self.db.flush(); seen = set(); count = 0
            for bookmaker in rows[0].get("bookmakers", []):
                name = bookmaker.get("name", "API-Football")
                for bet in bookmaker.get("bets", []):
                    if bet.get("id") not in BET_IDS: continue
                    for value in bet.get("values", []):
                        market, selection = _map_value(bet.get("id"), value)
                        try: odd = float(value.get("odd"))
                        except (TypeError, ValueError): continue
                        if not market or odd <= 1: continue
                        key = (name, market, selection)
                        if key in seen: continue
                        seen.add(key); opening_odd = float(opening.get(key, odd))
                        if previous.get(key) != odd:
                            self.db.add(OddsSnapshot(match_id=match.id, bookmaker=name, market=market, selection=selection, decimal_odds=odd, implied_probability=round(1 / odd, 6), captured_at=captured_at))
                        self.db.add(Odds(match_id=match.id, bookmaker=name, market=market, selection=selection, decimal_odds=odd, implied_probability=round(1 / odd, 6), opening_odds=opening_odd, movement=round((odd - opening_odd) / opening_odd, 6), source_type="api_football", is_fallback=False, fetched_at=captured_at)); count += 1
        return count

def _map_value(bet_id: int, value: dict) -> tuple[str | None, str]:
    label = str(value.get("value", "")).strip(); low = label.lower()
    if bet_id == 1:
        mapped = {"home": "home_win", "draw": "draw", "away": "away_win"}.get(low); return mapped, mapped or low
    if bet_id == 5:
        found = re.search(r"(over|under)\s*([0-9]+(?:[.,][0-9]+)?)", low)
        direction = found.group(1) if found else low
        point = found.group(2).replace(",", ".") if found else str(value.get("handicap", "")).strip()
        if direction in {"over", "under"} and point: return f"{direction}_{point}", f"{direction}_{point}"
    if bet_id == 8 and low in {"yes", "no"}: return f"btts_{low}", f"btts_{low}"
    if bet_id == 12:
        normalized = low.replace(" ", "")
        mapped = {"1x": "double_chance_1x", "home/draw": "double_chance_1x", "homeordraw": "double_chance_1x", "x2": "double_chance_x2", "draw/away": "double_chance_x2", "awayordraw": "double_chance_x2", "12": "double_chance_12", "home/away": "double_chance_12", "homeoraway": "double_chance_12"}.get(normalized)
        return mapped, mapped or label
    return None, label


def _map_outcome(market_key: str, outcome: dict, event: dict) -> tuple[str | None, str]:
    """Compatibility mapper retained for legacy market-mapping tests."""
    label = str(outcome.get("name", "")).strip(); low = label.lower()
    if market_key == "btts" and low in {"yes", "no"}: return f"btts_{low}", f"btts_{low}"
    home, away = str(event.get("home_team", "")).lower(), str(event.get("away_team", "")).lower()
    if market_key == "double_chance":
        if home in low and "draw" in low: return "double_chance_1x", "double_chance_1x"
        if away in low and "draw" in low: return "double_chance_x2", "double_chance_x2"
    if market_key == "draw_no_bet":
        if low == home: return "dnb_home", "dnb_home"
        if low == away: return "dnb_away", "dnb_away"
    return None, label
