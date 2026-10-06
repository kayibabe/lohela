from fastapi.routing import APIRoute
import inspect

from app.api.security import require_admin_access, require_pro_access
from app.api.v1 import admin, backtests, custom_accumulators, tickets


def _route(path: str, method: str, router):
    return next(
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method in route.methods
    )


def test_admin_routes_require_research_access():
    for route in admin.router.routes:
        if isinstance(route, APIRoute):
            dependency_callables = {
                dependency.call for dependency in route.dependant.dependencies
            }
            assert require_admin_access in dependency_callables, route.path


def test_backtest_run_accepts_authenticated_admin_or_research_key():
    route = _route("/backtests/run", "POST", backtests.router)
    dependency_callables = {
        dependency.call for dependency in route.dependant.dependencies
    }
    assert require_admin_access in dependency_callables


def test_daily_tickets_do_not_publish_internal_best_value():
    source = inspect.getsource(tickets.get_daily_tickets)
    assert "include_internal=include_internal" in source
    assert "visible_ticket_types = list(TicketType) if include_internal else list(PUBLIC_TICKET_TYPES)" in source
    assert "AccumulatorTicket.ticket_type.in_(visible_ticket_types)" in source
    route = _route("/tickets/daily", "GET", tickets.router)
    include_internal = next(
        parameter
        for parameter in route.dependant.query_params
        if parameter.name == "include_internal"
    )
    assert include_internal.default is False


def test_ticket_history_defaults_to_public_stream():
    route = _route("/tickets/history", "GET", tickets.router)
    include_internal = route.dependant.query_params[1]
    assert include_internal.default is False


def test_custom_accumulator_mutations_require_authenticated_pro_access():
    for route in custom_accumulators.router.routes:
        if isinstance(route, APIRoute) and route.path in {"", "/{accumulator_id}"}:
            dependency_callables = {
                dependency.call for dependency in route.dependant.dependencies
            }
            assert require_pro_access in dependency_callables, route.path
