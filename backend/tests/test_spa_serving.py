"""The bundled SPA must never shadow the API surface.

The React client calls same-origin relative paths, so production serves the
build from this app. The catch-all that enables client-side deep links is the
risky part: it must not swallow API 404s, /docs or /health, and it must not
serve files from outside the build directory.

Skipped unless a build has been staged at backend/static (docker build does
this; local dev serves the SPA from Vite instead).
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import FRONTEND_DIR, app

pytestmark = pytest.mark.skipif(
    not (FRONTEND_DIR / "index.html").is_file(),
    reason="no frontend build staged at backend/static",
)


@pytest.fixture(scope="module")
def client():
    # Plain TestClient skips lifespan, so these need no database.
    return TestClient(app)


def _spa_handler():
    for route in app.routes:
        if getattr(route, "name", "") == "serve_spa":
            return route.endpoint
    raise AssertionError("serve_spa route is not registered")


@pytest.mark.parametrize("path", ["/", "/tracker", "/analytics", "/admin"])
def test_client_routes_return_the_html_shell(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_unknown_api_path_stays_a_json_404(client):
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", ["/health", "/docs", "/openapi.json"])
def test_reserved_paths_are_not_shadowed(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "<div id=\"root\">" not in response.text


def test_static_assets_keep_their_content_type(client):
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg")


@pytest.mark.parametrize(
    "payload",
    [
        "../../../../etc/passwd",
        "../../requirements.txt",
        "../app/config.py",
        "assets/../../app/main.py",
    ],
)
def test_traversal_cannot_escape_the_build_directory(payload):
    """Raw payloads, not client-normalised ones, hit the containment check."""
    handler = _spa_handler()
    result = asyncio.run(handler(payload))
    served = Path(result.path).resolve()
    index = (FRONTEND_DIR / "index.html").resolve()
    assert served == index or FRONTEND_DIR in served.parents, f"escaped: {served}"
