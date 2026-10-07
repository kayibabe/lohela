from types import SimpleNamespace

from app.models import TicketType
from app.services.daily_accumulator import (
    daily_accumulator_source_snapshots,
    merged_source_selection_provenance,
    merged_source_selections,
)


def selection(match_id: int, position: int, picked: str):
    return SimpleNamespace(match_id=match_id, position=position, selection=picked)


def ticket(ticket_type, *selections, ticket_id=1, version=1):
    return SimpleNamespace(
        ticket_type=ticket_type,
        selections=list(selections),
        id=ticket_id,
        version=version,
        model_version="0.3.0",
        publication_hash=f"hash-{ticket_id}-{version}",
        published_at=SimpleNamespace(isoformat=lambda: "2026-10-07T06:00:00+00:00"),
    )


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


def test_merge_records_source_and_conservative_conflict_priority():
    conservative = ticket(TicketType.SAFE, selection(10, 1, "home_win"), ticket_id=46, version=2)
    balanced = ticket(TicketType.BALANCED, selection(10, 1, "away_win"), ticket_id=47, version=3)

    merged = merged_source_selection_provenance([balanced, conservative])

    assert [(row.match_id, source.id, source.ticket_type, source.version, conflict) for row, source, conflict in merged] == [
        (10, 46, TicketType.SAFE, 2, True),
    ]


def test_source_snapshots_change_when_a_source_ticket_is_republished():
    first = daily_accumulator_source_snapshots(
        {
            TicketType.SAFE: ticket(TicketType.SAFE, ticket_id=46, version=1),
            TicketType.BALANCED: ticket(TicketType.BALANCED, ticket_id=47, version=1),
        }
    )
    current = daily_accumulator_source_snapshots(
        {
            TicketType.SAFE: ticket(TicketType.SAFE, ticket_id=48, version=2),
            TicketType.BALANCED: ticket(TicketType.BALANCED, ticket_id=49, version=2),
        }
    )

    assert first != current
    assert first["safe"]["version"] == 1
    assert current["safe"]["version"] == 2
