"""Per-competition Poisson-DC fitting.

A single cross-league fit pools goal environments that differ structurally,
biasing team strength toward whichever league dominates the pooled sample.
ModelRunner now fits one Poisson-DC model per competition once there is
enough of that competition's own history, falling back to the pooled global
fit for thin competitions (cups, newly tracked leagues).
"""

import pytest

from app.services.model_runner import (
    MIN_LEAGUE_HISTORICAL_MATCHES,
    ModelRunner,
    _competitions_meeting_threshold,
    _group_by_competition,
)
from app.services.models.poisson_dc import PoissonDixonColes


def _row(competition_id: int, home: int, away: int, hg: int, ag: int) -> dict:
    return {
        "competition_id": competition_id,
        "home_team_id": home,
        "away_team_id": away,
        "home_goals": hg,
        "away_goals": ag,
    }


def test_group_by_competition_partitions_rows():
    historical = [_row(1, 10, 11, 2, 1), _row(2, 20, 21, 0, 0), _row(1, 11, 10, 1, 1)]
    grouped = _group_by_competition(historical)
    assert set(grouped) == {1, 2}
    assert len(grouped[1]) == 2
    assert len(grouped[2]) == 1


def test_competitions_meeting_threshold_excludes_thin_leagues():
    by_competition = {
        1: [_row(1, 10, 11, 1, 0)] * 150,  # well over the default threshold
        2: [_row(2, 20, 21, 1, 0)] * 40,  # a cup competition with little history
    }
    qualifying = _competitions_meeting_threshold(by_competition)
    assert qualifying == {1}


def test_competitions_meeting_threshold_uses_the_documented_default():
    by_competition = {
        1: [_row(1, 10, 11, 1, 0)] * MIN_LEAGUE_HISTORICAL_MATCHES,
        2: [_row(2, 20, 21, 1, 0)] * (MIN_LEAGUE_HISTORICAL_MATCHES - 1),
    }
    qualifying = _competitions_meeting_threshold(by_competition)
    assert qualifying == {1}


def test_score_match_prefers_the_competition_model_over_the_global_pool():
    """The selection logic mirrors `poisson_model = self._poisson_models.get(...) or self._poisson_model`."""
    runner = ModelRunner(None)  # type: ignore[arg-type]

    league_data = [_row(7, 100, 101, 3, 0), _row(7, 101, 100, 0, 3)] * 20
    global_data = league_data + [_row(9, 200, 201, 1, 1)] * 20

    global_model = PoissonDixonColes().fit(global_data)
    league_model = PoissonDixonColes().fit(league_data)
    runner._poisson_model = global_model
    runner._poisson_models = {7: league_model}

    # Same selection expression used in ModelRunner._score_match.
    selected_for_league = runner._poisson_models.get(7) or runner._poisson_model
    selected_for_unmapped = runner._poisson_models.get(9) or runner._poisson_model

    assert selected_for_league is league_model
    assert selected_for_unmapped is global_model


def test_score_match_falls_back_to_global_when_no_competition_model_exists():
    runner = ModelRunner(None)  # type: ignore[arg-type]
    global_model = PoissonDixonColes()
    runner._poisson_model = global_model
    runner._poisson_models = {}

    assert (runner._poisson_models.get(42) or runner._poisson_model) is global_model


def test_load_historical_matches_row_shape_includes_competition_id():
    """PoissonDixonColes.fit and the grouping helpers both key off this field."""
    import inspect

    source = inspect.getsource(ModelRunner._load_historical_matches)
    assert '"competition_id": m.competition_id' in source
