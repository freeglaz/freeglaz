"""The runtime version constant must match pyproject.toml.

webapp/backend/version.py carries the version into the packaged apps (where
pyproject.toml is not bundled); pyproject.toml is canonical. This test fails if a
release bumps one and forgets the other.
"""
import tomllib
from pathlib import Path

from webapp.backend.version import __version__


def test_version_matches_pyproject():
    # repo root = webapp/backend/tests → parents[3]
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    canonical = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert __version__ == canonical, (
        f"webapp/backend/version.py ({__version__}) != "
        f"pyproject.toml ({canonical}) — bump both on release."
    )


def test_health_exposes_version():
    from fastapi.testclient import TestClient
    from webapp.backend.main import app
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["version"] == __version__
