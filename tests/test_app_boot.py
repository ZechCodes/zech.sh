"""Characterization test: both app configs still boot and wire up every site.

Building the app is what surfaces controller-import failures, route collisions
(litestar raises ``ImproperlyConfiguredException`` when two handlers claim one
path) and config-schema drift. That makes it the broadest single safety net for
the skrift 0.1.0a81 -> 0.2.0a18 upgrade.

Production (``app.yaml``) is covered as well as development, because the two
differ in ways that matter: only production declares ``subdomain: dump`` on the
``post`` page type, and only production registers the custom API-key admin
controller that collides with skrift 0.2.0's built-in one.

Each boot runs in a subprocess — ``skrift.config.set_config_path`` and
``skrift.asgi`` both carry process-wide state, and pinning a config here must
not leak into the rest of the suite. Credentials are placeholders: nothing
connects at build time.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PLACEHOLDER_ENV = {
    "SECRET_KEY": "x" * 32,
    "DATABASE_URL": "postgresql+asyncpg://user:pass@127.0.0.1:5432/zech_sh",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "AUTH_REDIRECT_BASE_URL": "https://zech.sh",
    "GOOGLE_CLIENT_ID": "test-client-id",
    "GOOGLE_CLIENT_SECRET": "test-client-secret",
    "SPACES_ACCESS_KEY_ID": "test-key-id",
    "SPACES_SECRET_ACCESS_KEY": "test-secret",
}

BOOT = """
import json, os, sys
from pathlib import Path

sys.path.insert(0, {root!r})
os.chdir({root!r})
for key, value in {env!r}.items():
    os.environ.setdefault(key, value)

from skrift.config import set_config_path

set_config_path(Path({config!r}))

from skrift.asgi import create_app

app = create_app()
print("RESULT " + json.dumps({{
    "dispatcher": type(app).__name__,
    "domain": app.domain,
    "sites": sorted(app.site_apps),
}}))
"""


def boot(config: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", BOOT.format(root=str(ROOT), env=PLACEHOLDER_ENV, config=config)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.fail(f"app failed to boot from {config}:\n{proc.stderr}")
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


@pytest.fixture(scope="module")
def development() -> dict:
    return boot("app.development.yaml")


@pytest.fixture(scope="module")
def production() -> dict:
    return boot("app.yaml")


def test_development_config_builds_a_site_dispatcher(development):
    assert development["dispatcher"] == "SiteDispatcher"
    assert development["domain"] == "zech.sh"


def test_development_subdomains_each_get_an_app(development):
    assert development["sites"] == ["dump", "scan"]


def test_production_config_builds_a_site_dispatcher(production):
    assert production["dispatcher"] == "SiteDispatcher"
    assert production["domain"] == "zech.sh"


def test_production_subdomains_each_get_an_app(production):
    assert production["sites"] == ["aichat", "dump", "scan"]
