from datetime import date

from app.services.accumulator_builder import DailyTickets
from app.services.market_policy_shadow import snapshot_payload


def test_shadow_payload_is_explicitly_non_publishing_and_records_empty_tiers():
    built = DailyTickets(
        date(2026, 10, 4), 17, None, None, None, None, 0,
        {"pricing": {"source": "market"}}, [],
    )
    payload = snapshot_payload(built, frozenset({"draw"}))
    assert payload["mode"] == "shadow_only"
    assert payload["control_unchanged"] is True
    assert payload["excluded_markets"] == ["draw"]
    assert payload["public_ticket_count"] == 0
    assert payload["missing_public_ticket_types"] == ["safe", "balanced", "high_odds"]
