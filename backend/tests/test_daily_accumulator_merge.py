from types import SimpleNamespace

from app.models import TicketType
from app.services.daily_accumulator import merged_source_selections


def selection(match_id: int, position: int, picked: str):
    return SimpleNamespace(match_id=match_id, position=position, selection=picked)


def ticket(ticket_type, *selections):
    return SimpleNamespace(ticket_type=ticket_type, selections=list(selections))


def test_merge_is_union_and_conservative_wins_conflicts():
    conservative = ticket(
        TicketType.SAFE,
        selection(10, 1, "home_win"),
        selection(20, 2, "under_3.5"),
    )
    balanced = ticket(
        TicketType.BALANCED,
        selection(10, 1, "away_win"),
        selection(30, 2, "btts_yes"),
    )

    merged = merged_source_selections([balanced, conservative])

    assert [(row.match_id, row.selection) for row in merged] == [
        (10, "home_win"),
        (20, "under_3.5"),
        (30, "btts_yes"),
    ]


def test_merge_preserves_source_order_with_balanced_additions():
    merged = merged_source_selections(
        [
            ticket(TicketType.SAFE, selection(1, 2, "home_win")),
            ticket(
                TicketType.BALANCED,
                selection(2, 2, "draw"),
                selection(3, 1, "away_win"),
            ),
        ]
    )

    assert [row.match_id for row in merged] == [1, 3, 2]
