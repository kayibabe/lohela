"""
Data quality scoring — spec §26 Stage 2, §7.

Scores each match 0–100 based on availability of:
  Results/goals (+40 for finished matches) — primary training signal
  xG (+20), injuries (+15), form data (+20), stats (+25)

Matches <60 are flagged; <40 excluded from model runs (spec §26 Stage 2: >20% missing data).

For historical/backtesting ingestion without enrichment, a FINISHED match with
goals present scores at least 50 (results + form base), making it eligible for
model training and backtesting.
"""

from app.models import Match


def compute_prematch_quality_score(
    *,
    identity_complete: bool,
    minimum_history_matches: int,
    teams_with_form: int,
    bookmaker_count: int,
    market_family_count: int,
    has_team_news: bool,
    league_reliability: float,
) -> tuple[float, dict[str, float]]:
    """Score only information that can genuinely exist before kickoff.

    Finished-match result fields are deliberately excluded. Historical coverage,
    model-ready form and current odds replace the post-match result/statistics
    components used by ``compute_quality_score``.
    """
    history = (
        25.0 if minimum_history_matches >= 10
        else 20.0 if minimum_history_matches >= 5
        else 10.0 if minimum_history_matches >= 3
        else 0.0
    )
    form = 20.0 if teams_with_form >= 2 else 10.0 if teams_with_form == 1 else 0.0
    odds = (
        20.0 if bookmaker_count >= 3 and market_family_count >= 3
        else 15.0 if bookmaker_count >= 3 and market_family_count >= 2
        else 10.0 if bookmaker_count >= 1 and market_family_count >= 1
        else 0.0
    )
    components = {
        "fixture_identity": 15.0 if identity_complete else 0.0,
        "historical_coverage": history,
        "form_and_team_strength": form,
        "odds_coverage": odds,
        "team_news": 10.0 if has_team_news else 0.0,
        "league_reliability": 10.0 * max(0.0, min(1.0, league_reliability)),
    }
    return min(100.0, sum(components.values())), components


def compute_quality_score(match: Match, stats_raw: list[dict], injuries_raw: list[dict]) -> float:
    from app.models.match import MatchStatus
    score = 0.0

    # Actual match result available? (+40) — the most critical signal for training
    # A finished match with goals is always usable for model fitting (spec §38.1)
    if match.status == MatchStatus.FINISHED and match.home_goals is not None and match.away_goals is not None:
        score += 40.0

    # xG available? +20
    if match.home_xg is not None and match.away_xg is not None:
        score += 20.0

    # Injury/team news available? +10
    if injuries_raw:
        score += 10.0

    # Stats (shots, possession) available? +15
    if stats_raw and len(stats_raw) >= 2:
        has_shots = any(
            any(s.get("type") == "Total Shots" and s.get("value") is not None
                for s in ts.get("statistics", []))
            for ts in stats_raw
        )
        if has_shots:
            score += 15.0

    # Base form slot — enricher adds more (+15) when ≥5 prior matches exist
    score += 5.0

    # Odds slot — +10 when odds are ingested
    # Checked separately via odds_available flag in pipeline

    return min(score, 100.0)


def rescore_finished_match(match: Match) -> float:
    """
    Rescore a FINISHED match that was bulk-ingested without enrichment.
    Awards the result bonus so it qualifies for model training.
    """
    return compute_quality_score(match, [], [])


def is_excluded(data_quality_score: float) -> bool:
    """Match is excluded from model runs when quality < 40 (spec §26 Stage 2)."""
    from app.config import settings
    return data_quality_score < settings.min_data_quality_score


def is_flagged(data_quality_score: float) -> bool:
    """Match is flagged (low-confidence) when quality < 60."""
    from app.config import settings
    return data_quality_score < settings.warn_data_quality_score
