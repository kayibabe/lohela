from fastapi.routing import APIRoute
import inspect

from app.api.security import require_admin_access
from app.api.v1 import admin, tickets


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


def test_daily_tickets_do_not_publish_internal_best_value():
    source = inspect.getsource(tickets.get_daily_tickets)
    assert "include_internal=include_internal" in source
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
