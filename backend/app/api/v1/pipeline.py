"""Pipeline run observability API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.pipeline_tracker import get_pipeline_run

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/runs/{run_id}")
async def pipeline_run(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await get_pipeline_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return {
        "run_id": run.id,
        "target_date": run.target_date.isoformat(),
        "status": run.status.value,
        "current_stage": run.current_stage,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_details": run.error_details,
        "stages": [
            {
                "stage": stage.stage_name,
                "order": stage.stage_order,
                "status": stage.status.value,
                "retry_count": stage.retry_count,
                "input_count": stage.input_count,
                "output_count": stage.output_count,
                "started_at": stage.started_at.isoformat() if stage.started_at else None,
                "completed_at": stage.completed_at.isoformat() if stage.completed_at else None,
                "error_details": stage.error_details,
                "details": stage.stage_details,
            }
            for stage in run.stages
        ],
    }
