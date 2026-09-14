"""
Stage 1 & 2 of the Lohela pipeline â€” spec Â§26.

Pulls fixtures, team statistics, injuries, and xG data from API-Football.
Supports both direct (dashboard.api-football.com) and RapidAPI hosts via
the API_FOOTBALL_HOST env var.

Rate limit: 100 req/min â€” enforced via asyncio.Semaphore.
Retry: hand-rolled in APIFootballClient._get â€” 5 attempts, exponential
backoff on 502/503/504, and Retry-After honoured on 429.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings, cat_day_bounds_utc, cat_today
from app.models import Competition, Team, Match, MatchStatus, TeamStats
from app.services import cache as _cache

logger = logging.getLogger(__name__)

# 100 req/min â†’ ~1.67 req/s; semaphore of 5 with 0.05s sleep gives ~100/min headroom
_API_SEMAPHORE = asyncio.Semaphore(5)


class APIFootballClient:
    """Async HTTP client for API-Football v3."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "APIFootballClient":
        self._client = httpx.AsyncClient(
            base_url=settings.api_football_base_url,
            headers=settings.api_football_headers,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def get(
        self,
        endpoint: str,
        params: dict,
        cache_prefix: str = "fixtures",
        cache_ttl: int | None = None,
        force_refresh: bool = False,
    ) -> dict:
        """GET with Redis cache, retry on transient errors and 429 rate-limit back-off."""
        ttl = cache_ttl if cache_ttl is not None else settings.cache_ttl_fixtures
        cache_key = _cache.make_key(cache_prefix, {"endpoint": endpoint, **params})

        if not force_refresh:
            cached = await _cache.get(cache_key)
            if cached is not None:
                return cached

        max_attempts = 5
        resp = None
        for attempt in range(1, max_attempts + 1):
            async with _API_SEMAPHORE:
                resp = await self._client.get(endpoint, params=params)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                logger.warning("429 rate-limited â€” waiting %ds (attempt %d/%d)", retry_after, attempt, max_attempts)
                await asyncio.sleep(retry_after)
                continue

            if resp.status_code in (502, 503, 504) and attempt < max_attempts:
                wait = 2 ** attempt
                logger.warning("HTTP %d â€” retrying in %ds", resp.status_code, wait)
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            await asyncio.sleep(0.7)  # ~85 req/min â€” stay under free plan 100 req/min cap
            data = resp.json()
            if not force_refresh and ttl > 0:
                await _cache.set(cache_key, data, ttl)
            return data

        raise httpx.HTTPStatusError("Max retries exceeded", request=resp.request, response=resp)

    async def get_fixtures(
        self,
        league_id: int,
        season: int,
        from_date: date,
        to_date: date,
        cache_ttl: int = 300,
    ) -> list[dict]:
        data = await self.get("/fixtures", {
            "league": league_id,
            "season": season,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "timezone": "Africa/Blantyre",
        }, cache_ttl=cache_ttl)
        return data.get("response", [])

    async def get_fixture_result(self, fixture_id: int) -> dict | None:
        """Fetch one tracked fixture directly, bypassing caches used for ingestion."""
        data = await self.get(
            "/fixtures",
            {"id": fixture_id},
            cache_prefix="fixture-results",
            cache_ttl=0,
            force_refresh=True,
        )
        fixtures = data.get("response", [])
        return fixtures[0] if fixtures else None

    async def get_live_fixtures(self) -> list[dict]:
        """Return all currently active fixtures from API-Football."""
        data = await self.get(
            "/fixtures", {"live": "all", "timezone": "Africa/Blantyre"},
            cache_prefix="live-fixtures", cache_ttl=20,
        )
        return data.get("response", [])

    async def get_fixture_statistics(self, fixture_id: int) -> list[dict]:
        data = await self.get(
            "/fixtures/statistics", {"fixture": fixture_id},
            cache_prefix="stats", cache_ttl=settings.cache_ttl_stats,
        )
        return data.get("response", [])

    async def get_injuries(self, fixture_id: int) -> list[dict]:
        data = await self.get(
            "/injuries", {"fixture": fixture_id},
            cache_prefix="injuries", cache_ttl=settings.cache_ttl_stats,
        )
        return data.get("response", [])

    async def get_teams(self, league_id: int, season: int) -> list[dict]:
        data = await self.get("/teams", {"league": league_id, "season": season})
        return data.get("response", [])


class FixtureIngestor:
    """Orchestrates Stage 1 & 2 ingestion for a date range."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def refresh_live_status(self) -> dict:
        """Refresh only live fixtures, without running enrichment or models.

        `finished` counts matches that transitioned to FINISHED during this
        refresh, so callers can trigger immediate settlement.
        """
        stats = {"fetched": 0, "updated": 0, "skipped": 0, "unmatched": 0, "finished": 0}
        async with APIFootballClient() as client:
            fixtures = await client.get_live_fixtures()
            stats["fetched"] = len(fixtures)
            for fixture_data in fixtures:
                league_id = fixture_data.get("league", {}).get("id")
                if not league_id:
                    stats["skipped"] += 1
                    continue
                competition = await self._get_or_skip_competition(league_id)
                if competition is None:
                    stats["unmatched"] += 1
                    continue
                fixture_id = fixture_data.get("fixture", {}).get("id")
                prior_status = await self._current_status(fixture_id)
                action = await self._upsert_match(client, fixture_data, competition, skip_enrichment=True)
                stats["updated" if action == "updated" else "skipped"] += 1
                incoming_status = _map_status(
                    fixture_data.get("fixture", {}).get("status", {}).get("short", "NS")
                )
                if (
                    action != "skipped"
                    and prior_status != MatchStatus.FINISHED
                    and incoming_status == MatchStatus.FINISHED
                ):
                    stats["finished"] += 1
            await self.db.commit()
        logger.info("Live status refresh complete: %s", stats)
        return stats

    async def _current_status(self, api_football_id: int | None) -> MatchStatus | None:
        if not api_football_id:
            return None
        result = await self.db.execute(
            select(Match.status).where(Match.api_football_id == api_football_id)
        )
        return result.scalar_one_or_none()

    async def refresh_tracked_results(self, from_date: date, to_date: date) -> dict:
        """Force-refresh unresolved fixtures whose kickoff has already passed.

        Result ingestion must not reuse fixture-list cache entries. A fixture
        disappears from the live feed when it finishes, while a cached league
        response can still describe it as scheduled or live. Refreshing every
        unresolved started match also keeps prediction-only match cards current;
        settlement must not depend on a match appearing on a published ticket.
        """
        start_utc, _ = cat_day_bounds_utc(from_date)
        _, end_utc = cat_day_bounds_utc(to_date)
        now_utc = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(Match)
            .where(
                Match.kickoff_at >= start_utc,
                Match.kickoff_at < end_utc,
                Match.kickoff_at <= now_utc,
                Match.status.in_((MatchStatus.SCHEDULED, MatchStatus.LIVE)),
            )
            .order_by(Match.kickoff_at, Match.id)
        )
        matches = result.scalars().all()
        stats = {
            "matches_tracked": len(matches),
            "fixtures_fetched": 0,
            "updated": 0,
            "finished": 0,
            "missing": 0,
            "errors": [],
        }
        async with APIFootballClient() as client:
            competitions: dict[int, Competition] = {}
            for match in matches:
                try:
                    fixture = await client.get_fixture_result(match.api_football_id)
                    if fixture is None:
                        stats["missing"] += 1
                        continue
                    stats["fixtures_fetched"] += 1
                    competition = competitions.get(match.competition_id)
                    if competition is None:
                        competition = await self.db.get(Competition, match.competition_id)
                        if competition is None:
                            raise ValueError(f"Competition {match.competition_id} not found")
                        competitions[match.competition_id] = competition
                    if await self._upsert_match(client, fixture, competition, skip_enrichment=True) == "updated":
                        stats["updated"] += 1
                    if _map_status(fixture.get("fixture", {}).get("status", {}).get("short", "NS")) == MatchStatus.FINISHED:
                        stats["finished"] += 1
                except Exception as exc:
                    logger.warning("Result refresh failed for fixture %s: %s", match.api_football_id, exc)
                    stats["errors"].append({"fixture_id": match.api_football_id, "error": str(exc)})
            await self.db.commit()
        logger.info("Tracked result refresh complete: %s", stats)
        return stats

    async def ingest(
        self,
        from_date: date | None = None,
        to_date: date | None = None,
        season: int | None = None,
        skip_enrichment: bool | None = None,
    ) -> dict:
        """Pull fixtures for `from_date` to `to_date` (default: today + 48h).

        Pass `season` explicitly to pull historical data (e.g. season=2024 for
        the 2024/25 season) â€” useful on free API plans limited to 2022â€“2024.

        `skip_enrichment`: when True, skips per-fixture stats/injury API calls.
        Defaults to True for historical ranges (>7 days) to preserve API quota.
        """
        today = cat_today()
        from_date = from_date or today
        to_date = to_date or today + timedelta(days=2)
        season = season or _current_season()

        # Auto-skip enrichment calls for bulk historical pulls to conserve quota
        date_range_days = (to_date - from_date).days
        if skip_enrichment is None:
            skip_enrichment = date_range_days > 7

        stats = {"ingested": 0, "updated": 0, "skipped": 0, "leagues_processed": 0}

        async with APIFootballClient() as client:
            league_ids = list(dict.fromkeys(settings.tier1_league_ids + settings.tier2_league_ids))
            for i, league_id in enumerate(league_ids):
                competition = await self._get_or_skip_competition(league_id)
                if competition is None:
                    logger.warning("League %d not seeded in DB â€” run seed first", league_id)
                    continue

                fixtures = await client.get_fixtures(league_id, season, from_date, to_date)
                logger.info("League %d: %d fixtures fetched (%s to %s)", league_id, len(fixtures), from_date, to_date)

                for fixture_data in fixtures:
                    result = await self._upsert_match(client, fixture_data, competition, skip_enrichment=skip_enrichment)
                    stats[result] += 1

                stats["leagues_processed"] += 1
                # Pause between leagues to stay well under rate limit
                if i < len(league_ids) - 1:
                    await asyncio.sleep(2)

        total = stats["ingested"] + stats["updated"]
        if total < 20:
            logger.warning("LOW DATA WARNING: only %d matches ingested/updated (threshold: 20)", total)

        logger.info("Ingestion complete: %s", stats)
        return stats

    async def _get_or_skip_competition(self, league_id: int) -> Competition | None:
        result = await self.db.execute(
            select(Competition).where(Competition.api_football_id == league_id, Competition.active == True)
        )
        return result.scalar_one_or_none()

    async def _upsert_match(
        self,
        client: APIFootballClient,
        data: dict,
        competition: Competition,
        skip_enrichment: bool = False,
    ) -> str:
        """Insert or update a match record. Returns 'ingested', 'updated', or 'skipped'.

        skip_enrichment=True skips per-fixture stats/injury API calls to conserve
        API quota during bulk historical ingestion.
        """
        fixture = data.get("fixture", {})
        fixture_id = fixture.get("id")
        if not fixture_id:
            return "skipped"

        teams_data = data.get("teams", {})
        goals_data = data.get("goals", {})
        score_data = data.get("score", {})

        home_team = await self._get_or_create_team(teams_data.get("home", {}), competition)
        away_team = await self._get_or_create_team(teams_data.get("away", {}), competition)
        if not home_team or not away_team:
            return "skipped"

        existing = await self.db.execute(
            select(Match).where(Match.api_football_id == fixture_id)
        )
        match = existing.scalar_one_or_none()

        status_str = fixture.get("status", {}).get("short", "NS")
        status = _map_status(status_str)
        live_phase = _map_live_phase(status_str)
        elapsed_minutes = fixture.get("status", {}).get("elapsed")

        # Finished fixtures expose both statistics and injury data. Upcoming
        # fixtures expose injury/team-news coverage but no match statistics.
        stats_raw, injuries_raw = [], []
        injury_coverage_checked = False
        if not skip_enrichment:
            if status == MatchStatus.SCHEDULED:
                try:
                    injuries_raw = await client.get_injuries(fixture_id)
                    injury_coverage_checked = True
                except Exception as exc:
                    logger.warning("Injury enrichment failed for %s: %s", fixture_id, exc)
            else:
                stats_raw, injuries_raw = await asyncio.gather(
                    client.get_fixture_statistics(fixture_id),
                    client.get_injuries(fixture_id),
                    return_exceptions=True,
                )
                if isinstance(stats_raw, Exception):
                    stats_raw = []
                if isinstance(injuries_raw, Exception):
                    injuries_raw = []
                else:
                    injury_coverage_checked = True

        home_xg, away_xg = _extract_xg(stats_raw)
        team_news = _build_team_news(injuries_raw, coverage_checked=injury_coverage_checked)
        kickoff_at = _parse_kickoff(fixture.get("date"))

        if match is None:
            match = Match(
                api_football_id=fixture_id,
                competition_id=competition.id,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                kickoff_at=kickoff_at,
                status=status,
                live_phase=live_phase,
                elapsed_minutes=elapsed_minutes,
                season=str(data.get("league", {}).get("season", _current_season())),
                round=data.get("league", {}).get("round"),
                home_goals=goals_data.get("home"),
                away_goals=goals_data.get("away"),
                home_goals_ht=score_data.get("halftime", {}).get("home"),
                away_goals_ht=score_data.get("halftime", {}).get("away"),
                home_xg=home_xg,
                away_xg=away_xg,
                team_news=team_news,
            )
            self.db.add(match)
            await self.db.flush()
            await _upsert_team_stats(self.db, match, stats_raw)
            action = "ingested"
        else:
            match.status = status
            match.live_phase = live_phase
            match.elapsed_minutes = elapsed_minutes
            match.competition_id = competition.id
            match.home_team_id = home_team.id
            match.away_team_id = away_team.id
            match.kickoff_at = kickoff_at
            match.round = data.get("league", {}).get("round") or match.round
            match.home_goals = goals_data.get("home")
            match.away_goals = goals_data.get("away")
            if not skip_enrichment:
                match.home_xg = home_xg
                match.away_xg = away_xg
                match.team_news = team_news
            await _upsert_team_stats(self.db, match, stats_raw)
            action = "updated"

        from app.services.data_validator import compute_quality_score
        if not skip_enrichment:
            match.data_quality_score = compute_quality_score(match, stats_raw, injuries_raw)
            match.excluded_from_models = match.data_quality_score < settings.min_data_quality_score

        await self.db.flush()
        return action

    async def _get_or_create_team(self, team_data: dict, competition: Competition) -> Team | None:
        api_id = team_data.get("id")
        if not api_id:
            return None

        result = await self.db.execute(select(Team).where(Team.api_football_id == api_id))
        team = result.scalar_one_or_none()
        if team is None:
            team = Team(
                api_football_id=api_id,
                name=team_data.get("name", "Unknown"),
                competition_id=competition.id,
            )
            self.db.add(team)
            await self.db.flush()
        return team


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_kickoff(date_str: str | None) -> "datetime | None":
    """Parse ISO 8601 date string from API-Football into a datetime object."""
    if not date_str:
        return None
    from datetime import datetime
    try:
        # Python 3.11+ handles timezone offset in fromisoformat
        return datetime.fromisoformat(date_str)
    except ValueError:
        # Fallback for older formats
        import re
        date_str = re.sub(r"(\+\d{2}):(\d{2})$", r"+\1\2", date_str)
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S%z")


def _current_season() -> int:
    """API-Football season = year the season started (2025 for 2025/26)."""
    today = cat_today()
    return today.year if today.month >= 7 else today.year - 1


def _map_status(short: str) -> MatchStatus:
    live_codes = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
    if short in live_codes:
        return MatchStatus.LIVE
    if short == "FT" or short == "AET" or short == "PEN":
        return MatchStatus.FINISHED
    if short in {"PST", "CANC", "ABD", "AWD", "WO"}:
        return MatchStatus.POSTPONED
    return MatchStatus.SCHEDULED


def _map_live_phase(short: str) -> str | None:
    return {"1H": "1st_half", "HT": "half_time", "2H": "2nd_half", "ET": "extra_time", "BT": "penalties", "P": "penalties", "SUSP": "suspended", "INT": "interrupted", "LIVE": "live"}.get(short)


def _extract_xg(stats_raw: list[dict]) -> tuple[float | None, float | None]:
    home_xg = away_xg = None
    for team_stat in stats_raw:
        statistics = team_stat.get("statistics", [])
        for stat in statistics:
            if stat.get("type") == "expected_goals":
                value = stat.get("value")
                if value is None:
                    continue
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    continue
                if team_stat.get("team", {}).get("id") == stats_raw[0].get("team", {}).get("id") if stats_raw else False:
                    home_xg = value
                else:
                    away_xg = value
    # Simpler: just pull by index position (home=0, away=1)
    for i, team_stat in enumerate(stats_raw[:2]):
        statistics = team_stat.get("statistics", [])
        xg_val = next((s.get("value") for s in statistics if s.get("type") == "expected_goals"), None)
        if xg_val is not None:
            try:
                val = float(xg_val)
                if i == 0:
                    home_xg = val
                else:
                    away_xg = val
            except (ValueError, TypeError):
                pass
    return home_xg, away_xg


def _build_team_news(injuries_raw: list[dict], coverage_checked: bool = False) -> dict:
    if not injuries_raw:
        return {"reported_absences": []} if coverage_checked else {}
    news: dict[str, list] = {}
    seen: set[tuple] = set()
    for item in injuries_raw:
        player = item.get("player", {})
        team = item.get("team", {})
        team_name = team.get("name", "unknown")
        absence_key = (
            team.get("id"),
            player.get("id"),
            player.get("name"),
            player.get("reason"),
            player.get("type"),
        )
        if absence_key in seen:
            continue
        seen.add(absence_key)
        if team_name not in news:
            news[team_name] = []
        news[team_name].append({
            "name": player.get("name"),
            "reason": item.get("player", {}).get("reason"),
            "type": item.get("player", {}).get("type"),
        })
    return news


async def _upsert_team_stats(db: AsyncSession, match: Match, stats_raw: list[dict]) -> None:
    for i, team_stat in enumerate(stats_raw[:2]):
        is_home = i == 0
        team_api_id = team_stat.get("team", {}).get("id")
        if not team_api_id:
            continue

        team_result = await db.execute(select(Team).where(Team.api_football_id == team_api_id))
        team = team_result.scalar_one_or_none()
        if not team:
            continue

        existing = await db.execute(
            select(TeamStats).where(TeamStats.match_id == match.id, TeamStats.team_id == team.id)
        )
        ts = existing.scalar_one_or_none()
        statistics = {s.get("type"): s.get("value") for s in team_stat.get("statistics", [])}

        def safe_int(v):
            try:
                return int(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        def safe_float(v):
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        if ts is None:
            ts = TeamStats(
                match_id=match.id,
                team_id=team.id,
                is_home=is_home,
                shots=safe_int(statistics.get("Total Shots")),
                shots_on_target=safe_int(statistics.get("Shots on Goal")),
                possession=safe_float(str(statistics.get("Ball Possession", "")).replace("%", "")),
            )
            db.add(ts)
        else:
            ts.shots = safe_int(statistics.get("Total Shots"))
            ts.shots_on_target = safe_int(statistics.get("Shots on Goal"))
            ts.possession = safe_float(str(statistics.get("Ball Possession", "")).replace("%", ""))
