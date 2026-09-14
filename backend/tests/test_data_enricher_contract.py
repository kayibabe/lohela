"""Structural contract for DataEnricher.

`enrich_all_finished` was once indented into the module-level `_market_family`
helper, which silently removed it from the class and made every call to
POST /api/v1/models/enrich-form and /calibrate raise AttributeError. These
tests pin the public surface so an indentation slip fails here instead of in
production.
"""

import inspect

from app.services.data_enricher import DataEnricher, _market_family


def test_enricher_exposes_the_methods_the_admin_endpoints_call():
    for name in ("enrich_all_finished", "enrich_all_scheduled", "enrich_form"):
        assert callable(getattr(DataEnricher, name, None)), f"DataEnricher.{name} is missing"


def test_market_family_stays_module_level():
    assert not hasattr(DataEnricher, "_market_family")
    assert _market_family("over_2.5") == "totals"
    assert _market_family("double_chance_12") == "double_chance"
    assert _market_family("dnb_home") == "draw_no_bet"
    assert _market_family("home_win") == "match_result"


def test_scheduled_enrichment_uses_timezone_aware_day_bounds():
    """Day bounds must come from cat_day_bounds_utc, not naive local midnights.

    Match.kickoff_at is timezone-aware; comparing it against
    datetime.combine(target, ...) raises or silently mis-windows depending on
    the driver.
    """
    source = inspect.getsource(DataEnricher.enrich_all_scheduled)
    assert "cat_day_bounds_utc" in source
    assert "datetime.combine" not in source
