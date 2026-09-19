from fastapi import APIRouter
from app.api.v1 import (
    ingest,
    models,
    selections,
    odds,
    tickets,
    bets,
    analytics,
    admin,
    accumulators,
    results,
    performance,
    loss_review,
    pipeline,
    backtests,
    custom_accumulators,
    singles,
)
from app.api import auth

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(ingest.router)
router.include_router(models.router)
router.include_router(selections.router)
router.include_router(odds.router)
router.include_router(tickets.router)
router.include_router(bets.router)
router.include_router(analytics.router)
router.include_router(admin.router)
router.include_router(accumulators.router)
router.include_router(results.router)
router.include_router(performance.router)
router.include_router(loss_review.router)
router.include_router(pipeline.router)
router.include_router(backtests.router)
router.include_router(custom_accumulators.router)
router.include_router(singles.router)
