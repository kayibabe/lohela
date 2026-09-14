"""Backfill recent results and prepare model inputs for a target date."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import AsyncSessionLocal
from app.services.data_enricher import DataEnricher
from app.services.fixture_ingestor import FixtureIngestor
from app.services.model_preparation import ModelPreparationService


def season_for(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


def season_segments(start: date, end: date) -> list[tuple[date, date, int]]:
    segments: list[tuple[date, date, int]] = []
    cursor = start
    while cursor <= end:
        season = season_for(cursor)
        boundary = date(season + 1, 6, 30)
        segment_end = min(end, boundary)
        segments.append((cursor, segment_end, season))
        cursor = segment_end + timedelta(days=1)
    return segments


async def bootstrap(target: date, lookback_days: int) -> None:
    start = target - timedelta(days=lookback_days)
    end = target - timedelta(days=1)
    async with AsyncSessionLocal() as db:
        summaries = []
        for segment_start, segment_end, season in season_segments(start, end):
            result = await FixtureIngestor(db).ingest(
                segment_start,
                segment_end,
                season=season,
                skip_enrichment=True,
            )
            await db.commit()
            summaries.append({"season": season, "from": str(segment_start), "to": str(segment_end), **result})
        preparation = await ModelPreparationService(db).prepare(target)
        enriched = await DataEnricher(db).enrich_all_scheduled(target)
        await db.commit()
        print({"segments": summaries, "model_preparation": preparation, "scheduled_enriched": enriched})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--lookback-days", type=int, default=180)
    args = parser.parse_args()
    asyncio.run(bootstrap(args.target_date, args.lookback_days))
