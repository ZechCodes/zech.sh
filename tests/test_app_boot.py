"""Characterization test: the app still boots and wires up every site.

Building the app is what surfaces controller-import failures, page-type route
collisions (skrift raises at startup when a generated public route collides with
a hand-written one) and config-schema drift. That makes it the broadest
single safety net for the skrift 0.1.0a81 -> 0.2.0a18 upgrade.

The boot runs in a subprocess: ``skrift.config.set_config_path`` and
``skrift.asgi`` both carry process-wide state, and pinning the dev config here
must not leak into the rest of the suite.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

BOOT = """
import json, os, sys
from pathlib import Path

sys.path.insert(0, {root!r})
os.chdir({root!r})
os.environ.setdefault("SECRET_KEY", "x" * 32)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")

from skrift.config import set_config_path

set_config_path(Path("app.development.yaml"))

from skrift.asgi import create_app

app = create_app()
print(json.dumps({{
    "dispatcher": type(app).__name__,
    "domain": app.domain,
    "sites": sorted(app.site_apps),
}}))
"""


@pytest.fixture(scope="module")
def booted() -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", BOOT.format(root=str(ROOT))],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.fail(f"app failed to boot from app.development.yaml:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_app_builds_a_site_dispatcher(booted):
    assert booted["dispatcher"] == "SiteDispatcher"


def test_primary_domain_is_configured(booted):
    assert booted["domain"] == "zech.sh"


def test_every_configured_subdomain_gets_an_app(booted):
    assert booted["sites"] == ["dump", "scan"]
