"""Closing-line-value capture.

Snapshots the best available market price at kickoff for every prediction
that had an entry price, so live performance can be judged against CLV —
whether the model consistently beats the closing line — rather than only
short-run win/loss, which is dominated by variance.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Match, OddsSnapshot, Prediction


async def capture_closing_odds(db: AsyncSession, *, limit: int = 500) -> dict:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Prediction, Match)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Match.kickoff_at <= now,
            Prediction.source_decimal_odds.is_not(None),
            Prediction.closing_decimal_odds.is_(None),
        )
        .limit(limit)
    )
    rows = result.all()
    captured = 0
    skipped_no_snapshot = 0
    for prediction, match in rows:
        snap_result = await db.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.match_id == match.id,
                OddsSnapshot.market == prediction.market,
                OddsSnapshot.selection == prediction.selection,
                OddsSnapshot.captured_at <= match.kickoff_at,
            )
        )
        snapshots = snap_result.scalars().all()
        if not snapshots:
            # No quote was ever captured before kickoff for this leg — we
            # genuinely don't know the closing price, so leave it null
            # rather than fabricate one from a post-kickoff quote.
            skipped_no_snapshot += 1
            continue

        # Last quote per bookmaker as of kickoff, then the best (highest)
        # price across bookmakers — the same "best odds" methodology used
        # to pick the entry price, so the comparison is apples-to-apples.
        latest_per_bookmaker: dict[str, OddsSnapshot] = {}
        for snap in snapshots:
            existing = latest_per_bookmaker.get(snap.bookmaker)
            if existing is None or snap.captured_at > existing.captured_at:
                latest_per_bookmaker[snap.bookmaker] = snap
        closing = max(latest_per_bookmaker.values(), key=lambda s: s.decimal_odds)

        prediction.closing_decimal_odds = closing.decimal_odds
        prediction.closing_implied_probability = closing.implied_probability
        prediction.closing_odds_at = closing.captured_at
        if prediction.source_decimal_odds and closing.decimal_odds:
            prediction.clv_percentage = round(
                prediction.source_decimal_odds / closing.decimal_odds - 1.0, 6
            )
        captured += 1

    if captured:
        await db.commit()
    return {"candidates": len(rows), "captured": captured, "skipped_no_snapshot": skipped_no_snapshot}
