"""Guard: NO DIRECT localStorage/sessionStorage access in the frontend (outside the helper).

localStorage can THROW during render in a WebKitGTK webview -> React tree unmounted ->
blank / crashed page (bit us twice: startup theme, then Logs page). Every access must go
through lib/safeLocalStorage.js. STATIC check (no JS runner) that runs with the pytest
suite — doubles the ESLint no-restricted-properties (which only runs at lint time).
"""
import re
from pathlib import Path

# .../webapp/backend/tests/ → parents[2] = webapp/
_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
_HELPER_REL = "lib/safeLocalStorage.js"

# DIRECT access = localStorage / sessionStorage followed by `.` (member) or `[` (indexed).
# i18n config strings ('localStorage' in order/caches) do not match (no . or [).
_DIRECT = re.compile(r"(?:local|session)Storage\s*[.\[]")


def test_no_direct_localstorage_access():
    files = list(_SRC.rglob("*.js")) + list(_SRC.rglob("*.jsx"))
    assert files, f"no frontend source found under {_SRC}"
    offenders = []
    for f in files:
        rel = f.relative_to(_SRC).as_posix()
        if rel == _HELPER_REL:
            continue  # the helper IS the only one allowed
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith(("//", "*", "/*")):
                continue  # comments
            if _DIRECT.search(line):
                offenders.append(f"{rel}:{i}: {s}")
    assert not offenders, (
        "Accès localStorage/sessionStorage DIRECT interdit (utiliser lib/safeLocalStorage.js) :\n"
        + "\n".join(offenders)
    )
