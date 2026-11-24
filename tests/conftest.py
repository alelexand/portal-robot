import os

import pytest

from demo_portal import PortalServer, create_app
from portal_robot import Settings

# Locally you can run the tests with an installed browser: TEST_BROWSER_CHANNEL=msedge pytest
CHANNEL = os.environ.get("TEST_BROWSER_CHANNEL", "")


@pytest.fixture
def portal_factory():
    servers = []

    def start(**kwargs) -> PortalServer:
        kwargs.setdefault("report_delay", (0.2, 0.4))
        server = PortalServer(create_app(**kwargs)).__enter__()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.__exit__(None, None, None)


@pytest.fixture
def settings_for():
    def make(server: PortalServer, **overrides) -> Settings:
        values = dict(base_url=server.url, username="demo", password="demo123", browser_channel=CHANNEL,
                      timeout_ms=8000)
        values.update(overrides)
        return Settings(**values)
    return make
