"""PUBLICATION guard: the production frontend bundle must NEVER enable
mocks by default (without an env variable).

Context: first-launch bug found on Linux (fresh clone without `.env.local`,
which is gitignored) → `npm run build` baked in `USE_MOCKS=true` → fake printer
`CN-MOCK-0001` + onboarding short-circuited. Fix = OPT-IN polarity (`=== 'true'`).

No JS runner in the project → STATIC check of the source (the simplest, and
it runs with the existing pytest suite, like the -nc / -S guards).
"""
from pathlib import Path

# .../webapp/backend/tests/ → parents[2] = webapp/
_CLIENT_JS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "client.js"


def test_mocks_are_opt_in():
    assert _CLIENT_JS.exists(), f"client.js introuvable : {_CLIENT_JS}"
    src = _CLIENT_JS.read_text(encoding="utf-8")
    # MANDATORY: mocks active ONLY if VITE_USE_MOCKS === 'true' (opt-in).
    assert "import.meta.env.VITE_USE_MOCKS === 'true'" in src, \
        "client.js doit lire VITE_USE_MOCKS en OPT-IN (=== 'true') — défaut = backend réel"
    # FORBIDDEN: the old opt-out polarity (mocks by default) would leak the mock
    # into a production build (regression of the first-launch bug).
    assert "VITE_USE_MOCKS !== 'false'" not in src, \
        "polarité opt-out interdite : le mock fuirait en build prod par défaut"
