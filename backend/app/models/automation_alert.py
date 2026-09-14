"""Durable operational alerts emitted only after automation exhausts retries."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class AutomationAlert(Base):
    __tablename__ = "automation_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(240), nullable=False, unique=True, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="error")
    task_name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
