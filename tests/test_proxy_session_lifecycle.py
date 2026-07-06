from __future__ import annotations

from llm_gateway.api.deps import session_dep
from llm_gateway.main import app
from fastapi.routing import APIRoute


def test_proxy_routes_do_not_depend_on_request_scoped_db_session():
    proxy_paths = {
        "/v1/chat/completions",
        "/v1/responses",
        "/v1/models",
    }

    routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path in proxy_paths
    }
    assert routes.keys() == proxy_paths

    for path, route in routes.items():
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert session_dep not in dependency_calls, path
