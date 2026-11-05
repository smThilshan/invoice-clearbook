"""Every route needs a logged-in user, except the few that must stay open (decision 71). A new
route is protected without anyone remembering to: this test fails if one is not."""

import re
import uuid

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

SETTINGS = Settings(_env_file=None, app_env="test", jwt_secret="made-up-secret-" + "x" * 40)
# Open on purpose: probes, logging in and out, and the provider's return (signed state).
OPEN = {
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/{slug}/callback"),
    ("GET", "/sharepoint/callback"),
}


def _example(parameter: re.Match[str]) -> str:
    """A value for a path parameter: an id where one is expected."""
    return str(uuid.uuid4()) if "id" in parameter.group() else "x"


def test_every_route_but_the_open_ones_refuses_a_request_without_a_session() -> None:
    app = create_app(SETTINGS)
    # The OpenAPI description lists every endpoint: public, whatever the routers' internals.
    routes = [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    ]
    open_now = set()
    with TestClient(app, follow_redirects=False) as client:  # a redirect is not a refusal
        for method, path in routes:
            url = re.sub(r"\{[^}]+\}", _example, path)
            answer = client.request(method, url, headers={"X-Clearbook": "1"})
            if answer.status_code != 401:
                open_now.add((method, path))

    assert len(routes) > 30  # the app's routes were really walked
    assert open_now == OPEN
