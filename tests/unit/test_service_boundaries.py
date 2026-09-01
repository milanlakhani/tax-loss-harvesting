from app.main import app as backend_app
from app.mcp_main import app as mcp_app


def test_backend_and_mcp_are_separate_asgi_apps() -> None:
    backend_paths = {route.path for route in backend_app.routes}

    assert "/mcp" not in backend_paths
    assert mcp_app is not backend_app
