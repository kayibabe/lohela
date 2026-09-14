"""Deduplicated persistence for actionable automation failures."""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationAlert


async def record_automation_alert(
    db: AsyncSession,
    *,
    dedupe_key: str,
    task_name: str,
    title: str,
    detail: str,
    target_date: date | None = None,
    context: dict | None = None,
    severity: str = "error",
) -> AutomationAlert:
    now = datetime.now(timezone.utc)
    alert = (await db.execute(select(AutomationAlert).where(AutomationAlert.dedupe_key == dedupe_key))).scalar_one_or_none()
    if alert is None:
        alert = AutomationAlert(
            dedupe_key=dedupe_key,
            task_name=task_name,
            target_date=target_date,
            title=title,
            detail=detail,
            context=context or {},
            severity=severity,
        )
        db.add(alert)
    else:
        alert.detail = detail
        alert.context = context or alert.context
        alert.occurrence_count += 1
        alert.last_seen_at = now
        alert.resolved = False
        alert.resolved_at = None
    await db.flush()
    return alert


async def resolve_automation_alert(db: AsyncSession, alert_id: int) -> bool:
    alert = await db.get(AutomationAlert, alert_id)
    if alert is None:
        return False
    alert.resolved = True
    alert.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return True


async def resolve_automation_alert_by_dedupe_key(db: AsyncSession, dedupe_key: str) -> bool:
    """Auto-clear a previously recorded alert once its condition stops recurring
    (e.g. a same-day catch-up run later meets the minimum-ticket floor)."""
    alert = (
        await db.execute(select(AutomationAlert).where(AutomationAlert.dedupe_key == dedupe_key))
    ).scalar_one_or_none()
    if alert is None or alert.resolved:
        return False
    alert.resolved = True
    alert.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return True
