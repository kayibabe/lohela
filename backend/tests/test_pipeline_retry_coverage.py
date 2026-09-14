"""Stage tracking must never sit outside the retried region.

Every tracked task used to mark its stage RUNNING before entering `try`. A
transient DB error there escaped the handler, so `self.retry` never ran and the
`task_failure` hook suppressed the alert (it saw retries < max_retries). The
run then hung until the watchdog swept it up.
"""

import inspect
import re

import pytest

from app.tasks import pipeline

TRACKED_TASKS = [
    "ingest_fixtures",
    "fetch_odds",
    "enrich_data",
    "run_models",
    "generate_tickets",
    "settle_results",
    "aggregate_performance",
]


def _source(task_name: str) -> str:
    """Source of the undecorated task body.

    `@celery_app.task` returns a Task instance whose `.run` is the original
    function; `__wrapped__` is set when Celery keeps a reference to it.
    """
    task = getattr(pipeline, task_name)
    func = getattr(task, "__wrapped__", None) or getattr(task, "run", task)
    return inspect.getsource(func)


@pytest.mark.parametrize("task_name", TRACKED_TASKS)
def test_running_track_is_inside_the_try_block(task_name):
    source = _source(task_name)
    try_index = source.index("\n    try:")
    running = [m.start() for m in re.finditer(r"RunStatus\.RUNNING", source)]
    for position in running:
        assert position > try_index, (
            f"{task_name} marks RUNNING before `try:`; a tracking failure there "
            "escapes the retry handler"
        )


@pytest.mark.parametrize("task_name", TRACKED_TASKS)
def test_failure_path_tracking_cannot_mask_the_retry(task_name):
    """The except branch must use the best-effort tracker.

    A raw `_track` there can raise and replace the real task error, which both
    loses the diagnosis and skips `self.retry`.
    """
    source = _source(task_name)
    except_index = source.index("except Exception as exc:")
    tail = source[except_index:]
    assert "_track_quietly(" in tail, f"{task_name} tracks failure without _track_quietly"
    assert "_run_async(_track(" not in tail, (
        f"{task_name} still calls _track directly on the failure path"
    )


def test_track_quietly_swallows_tracking_errors(monkeypatch):
    def _boom(coro):
        coro.close()
        raise RuntimeError("tracker down")

    monkeypatch.setattr(pipeline, "_run_async", _boom)
    # Must not raise: the caller re-raises the real task error straight after.
    pipeline._track_quietly(1, ["stage"], "failed", error="original cause")
