"""Regression tests for scheduled model-version and publication lineage."""

import inspect

from app.api.v1.backtests import BacktestRequest
from app.config import CURRENT_MODEL_VERSION
from app.services.model_runner import ModelRunner
from app.tasks.pipeline import _daily_pipeline_canvas, generate_tickets, run_models


def test_current_model_version_is_used_by_all_automatic_defaults():
    assert CURRENT_MODEL_VERSION == "0.3.0"
    assert inspect.signature(ModelRunner).parameters["model_version"].default == CURRENT_MODEL_VERSION
    assert inspect.signature(run_models.run).parameters["model_version"].default == CURRENT_MODEL_VERSION
    assert BacktestRequest(
        period_start="2026-01-01", period_end="2026-01-31"
    ).model_version == CURRENT_MODEL_VERSION


def test_ticket_task_requires_upstream_model_run_lineage():
    parameters = list(inspect.signature(generate_tickets.run).parameters)
    assert parameters[:2] == ["model_result", "target_date"]


def test_daily_pipeline_runs_current_model_then_forwards_its_result_to_publication():
    canvas = _daily_pipeline_canvas("2026-08-28", 17)
    model_task = canvas.tasks[3]
    publication_task = canvas.tasks[4]

    assert model_task.args == ("2026-08-28", CURRENT_MODEL_VERSION, 17)
    assert model_task.immutable is True
    assert publication_task.args == ("2026-08-28", 17)
    assert publication_task.immutable is False
