# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Transform raw SOAP MediumList → webapp API Paper contract.

The ``parsers.parse_soap_medium_list`` parser already returns a rich
dict per paper (id, name, capabilities, profiles, details,
calibration…). This module maps those dicts to the **frozen contract**
of the UX spec:

.. code-block:: python

   {
     "mediaid": str,        # firmware id (hex 32 chars custom, short e.g. "2001" factory)
     "name": str,
     "category": str,       # "Custom Paper" | "Photo Paper" | "Fine Art Paper" | …
     "factory": bool,
     "custom": bool,        # not factory
     "locked": bool,
     "donor_id": str | None,    # short donor id for customs
     "donor_name": str | None,  # resolved donor name (factory lookup)
     "finish": str,         # "gloss" | "matte" | "canvas" | "film" | "other"
     "capabilities": {
       "borderless": bool, "ge": bool, "max_detail": bool, "scanning": bool,
     },
     "inks": {"pk": bool, "mk": bool},
     # Note: no "weight" / grammage field. The SOAP Details/Grammage
     # (g/sqm) field is NOT a real grammage — it's an internal HP
     # identifier that distinguishes opaque vs transparent (34/35
     # analyzed papers have the same value "70-90", only the
     # transparent Clear Film has a different value "174").
     # The ``freeglaz paper show`` CLI hides it by default
     # (accessible only via ``--json`` for debug).
     "icc": [               # 1 slot (GE not supported) or 2 slots (ge_off + ge_on)
       {
         "kind": "ge_off" | "ge_on" | "single",
         "name": str,
         "custom": bool,    # user profile vs factory HP
         "date": str | None,  # ISO YYYY-MM-DD
       },
       …
     ],
     "clc": {"date": str | None, "status": "valid" | "stale" | "never"},
     "last_used": str | None,  # ISO 8601, null if no history
     "favorite": bool,         # injected from paper_favorites.json
     "has_notes": bool,        # injected from paper_notes.json
   }

``favorite`` and ``has_notes`` are injected by the caller (route
``/api/papers``) from the ``paper_state`` services, not computed here.
"""
import logging
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Factory vs custom detection ──────────────────────────────────────


def _is_custom_mediaid(mediaid: Optional[str]) -> bool:
    """Determine whether a MEDIAID comes from a custom paper.

    Solid empirical criterion: customs have a 32-character hex hash
    MEDIAID (e.g. ``"9E489F02AE027F9DD93191D872728C1D"``), factory ones
    have a short numeric ID (e.g. ``"1000"``, ``"2090"``, ``"4060"``).

    Why not the XML ``<factory>0|1</factory>`` attribute? The Z9 firmware
    returns ``<factory>0</factory>`` for some factory papers added via a
    later firmware update, which makes them wrongly appear as personal
    customs in the UI. The MEDIAID format is more reliable because it's
    determined at paper creation (created host-side, e.g. freeglaz →
    hex 32; firmware ROM → short ID).
    """
    if not isinstance(mediaid, str) or len(mediaid) != 32:
        return False
    return all(c in "0123456789ABCDEFabcdef" for c in mediaid)


# ─── Finish detection ─────────────────────────────────────────────────

# Order matters: "matte" must match BEFORE "mat" so as not to catch
# "Matt's Photo" (unlikely but defensive). "canvas" / "film" are more
# specific keywords than the generic "gloss"/"matte".
_FINISH_KEYWORDS = (
    # (normalized keyword, finish_value)
    ("gloss",    "gloss"),
    ("lustre",   "gloss"),
    ("luster",   "gloss"),   # US variant
    ("satin",    "gloss"),
    ("baryta",   "gloss"),
    ("pearl",    "gloss"),
    ("metallic", "gloss"),
    ("matte",    "matte"),
    ("mat",      "matte"),   # after matte on purpose
    ("rag",      "matte"),
    ("cotton",   "matte"),
    ("canvas",   "canvas"),
    ("film",     "film"),
    ("backlit",  "film"),
    ("clear",    "film"),
)


def _normalize(s: Optional[str]) -> str:
    """Lowercase + strip accents (NFD → ASCII). Case- and
    accent-insensitive for ``_compute_finish`` matching."""
    if not s:
        return ""
    return (
        unicodedata.normalize("NFD", s)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def _compute_finish(name: Optional[str], donor_name: Optional[str]) -> str:
    """Detect a paper's finish by case- and accent-insensitive keyword
    matching. Looks in the paper name first, then in the donor name
    (useful for customs named "My paper 2026" based on
    "HP Universal Satin Canvas").

    Priority order: first keyword that matches in ``_FINISH_KEYWORDS``
    wins (the order is frozen in the const, cf. spec brief).
    """
    haystack = " ".join(filter(None, [_normalize(name), _normalize(donor_name)]))
    for keyword, finish in _FINISH_KEYWORDS:
        if keyword in haystack:
            return finish
    return "other"


# ─── Donor resolution ─────────────────────────────────────────────────


def _resolve_donor(
    donor_id: Optional[str],
    factory_papers_by_id: dict[str, dict],
) -> Optional[str]:
    """Resolve ``donor_id`` (short str e.g. "2090") to ``donor_name`` via
    lookup in the factory index. Returns None if not found."""
    if not donor_id:
        return None
    factory = factory_papers_by_id.get(donor_id)
    if not factory:
        logger.info(
            "papers: donor_id=%r not found in the factory list",
            donor_id,
        )
        return None
    return factory.get("name") or factory.get("short_name")


# ─── CLC status ───────────────────────────────────────────────────────


# Authoritative mapping LEDM `CalibrationStatus` → webapp contract.
# The SOAP `<Calibration>` distinguishes only 2 states (obsolete=0/1),
# the LEDM exposes 4. We keep SOAP as a fallback for MEDIAIDs absent
# from the LEDM (edge case of a freshly created custom paper not yet
# propagated) — cf. the ``calibration_ledm`` module docstring.
_LEDM_TO_CLC_STATUS = {
    "completed": "valid",
    "obsoleted": "stale",
    "pending":   "pending",
    "notDone":   "never",
}


def _clc_status(paper_raw: dict, ledm_entry: Optional[dict] = None) -> dict:
    """Determine the color linear calibration (CLC) state.

    Primary source = LEDM ``/Calibration/Calibration.xml``
    ``colorLinearization`` entry for this MEDIAID, passed via
    ``ledm_entry`` (cf. ``calibration_ledm.parse_color_linearization``).
    If present, its status is authoritative.

    Secondary source (fallback) = ``<Calibration>`` block of the SOAP
    ``getMediumList``. Used only if LEDM has no entry for this MEDIAID,
    or if LEDM was inaccessible (caller passes ``ledm_entry=None``).

    Returns ``{status, date}`` where:
    - ``status`` ∈ ``"valid" | "stale" | "pending" | "never"``
    - ``date``   ∈ ISO 8601 or ``None`` (present on valid + stale,
      absent on pending + never)
    """
    # ── LEDM primary source ───────────────────────────────────────
    if ledm_entry is not None:
        raw_status = (ledm_entry.get("status") or "").strip()
        mapped = _LEDM_TO_CLC_STATUS.get(raw_status, "never")
        timestamp = ledm_entry.get("timestamp")
        if mapped in ("pending", "never"):
            # No date for these 2 states (consistent with the contract)
            return {"status": mapped, "date": None}
        return {"status": mapped, "date": timestamp}

    # ── SOAP fallback ─────────────────────────────────────────────
    cal = paper_raw.get("calibration")
    if not cal or not cal.get("date"):
        return {"status": "never", "date": None}
    return {
        "status": "stale" if cal.get("obsolete") else "valid",
        "date":   cal["date"],
    }


# ─── ICC tickets selection ────────────────────────────────────────────


# Empirical firmware values for <GlossEnhancer> in a ProfilingTicket:
# - "OFF"      → ICC profile without Gloss Enhancer
# - "FULLPAGE" → ICC profile with full-page Gloss Enhancer enabled
# The P1.A brief assumed "ON" but the Z9 firmware uses "FULLPAGE".
# We keep "ON" for defensive compat in case a firmware revision
# normalizes the value.
_GE_ON_VALUES = {"FULLPAGE", "ON"}
_GE_OFF_VALUES = {"OFF"}


def _pick_icc_tickets(
    profiles_raw: list[dict],
    ge_supported: bool,
) -> list[dict]:
    """Select the most recent ICC profile per slot.

    - GE supported (``GlossEnhancerSupported == 1``) → 2 slots:
      ``ge_off`` (``gloss_enhancer == 'OFF'``) + ``ge_on``
      (``gloss_enhancer in {'FULLPAGE', 'ON'}``).
    - GE not supported → 1 ``single`` slot (the available profile, GE
      ignored firmware-side).

    For each slot, we take the profile with the most recent ``date``.
    If no profile matches, we don't emit a slot (silent skip — the UI
    will show "No profile for this slot").

    :param profiles_raw: list of dicts from the SOAP parser (fields
        ``custom``, ``date``, ``icc_name``, ``gloss_enhancer``, …)
    :param ge_supported: bool from ``capabilities['GlossEnhancerSupported']``.
    """
    if not profiles_raw:
        return []

    if ge_supported:
        ge_off = [p for p in profiles_raw if p.get("gloss_enhancer") in _GE_OFF_VALUES]
        ge_on  = [p for p in profiles_raw if p.get("gloss_enhancer") in _GE_ON_VALUES]
        out = []
        if ge_off:
            best = max(ge_off, key=_profile_date_key)
            out.append(_format_icc_slot(best, kind="ge_off"))
        if ge_on:
            best = max(ge_on, key=_profile_date_key)
            out.append(_format_icc_slot(best, kind="ge_on"))
        return out

    # GE not supported: a single slot with the most recent profile
    # (all gloss_enhancer values combined — usually empty or "OFF" on
    # papers without GE).
    best = max(profiles_raw, key=_profile_date_key)
    return [_format_icc_slot(best, kind="single")]


def _profile_date_key(profile: dict) -> str:
    """Date sort key — ISO YYYY-MM-DD string, lexicographic sort
    consistent with chronological sort. Profiles without a date go
    last (empty string key)."""
    return profile.get("date") or ""


def _format_icc_slot(profile: dict, *, kind: str) -> dict:
    """Build the contract dict from a raw profile."""
    return {
        "kind": kind,
        "name": profile.get("icc_name") or "",
        "custom": bool(profile.get("custom")),
        "date": profile.get("date") or None,
    }


# ─── Inks normalization ───────────────────────────────────────────────


def _normalize_bool(v) -> bool:
    """Normalize an XML string value "0"/"1" or bool to a Python bool."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return False


def _inks_from_details(details: dict) -> dict:
    """Extract ``{pk, mk}`` from the ``details`` block of the SOAP parser.

    Expected keys: ``Uses Photo Black`` and ``Uses Matte Black`` (string
    "0"/"1" in XML).
    """
    if not details:
        return {"pk": False, "mk": False}
    return {
        "pk": _normalize_bool(details.get("Uses Photo Black", False)),
        "mk": _normalize_bool(details.get("Uses Matte Black", False)),
    }


# Note: no _weight_from_details(). The SOAP Details/Grammage (g/sqm)
# field is NOT a real grammage (internal HP identifier, cf. the module
# docstring). We don't expose it in the webapp API Paper contract so as
# not to mislead the user. The raw data stays accessible via
# ``freeglaz paper show --json`` for debug.


# ─── Capabilities ─────────────────────────────────────────────────────


def _capabilities_normalized(raw_caps: dict) -> dict:
    """Map the 4 capabilities exposed to the contract from the raw SOAP
    dict (which contains ~20 various firmware flags).

    Choice of the 4 capabilities for P1 (cf. spec §5 Zone 1):
    - ``borderless``: borderless printing
    - ``ge``: selective freeglaz Gloss Enhancer possible
    - ``max_detail``: 1200 dpi high-definition mode
    - ``scanning``: scan-only profiling (for the P4/P5 roadmap)
    """
    if not raw_caps:
        return {
            "borderless": False, "ge": False,
            "max_detail": False, "scanning": False,
        }
    return {
        "borderless": _normalize_bool(raw_caps.get("Borderless", False)),
        "ge":         _normalize_bool(raw_caps.get("GlossEnhancerSupported", False)),
        "max_detail": _normalize_bool(raw_caps.get("MaxDetailSupported", False)),
        "scanning":   _normalize_bool(raw_caps.get("ScanningSupported", False)),
    }


# ─── Transformer principal ────────────────────────────────────────────


def transform_paper(
    raw: dict,
    *,
    factory_papers_by_id: dict[str, dict],
    favorites: Optional[dict] = None,
    notes_keys: Optional[set] = None,
    ledm_clc: Optional[dict[str, dict]] = None,
) -> dict:
    """Convert a raw dict (SOAP parser output) into a webapp API Paper
    contract dict.

    :param raw: dict from the SOAP MediumList parser
    :param factory_papers_by_id: index ``{factory_paper_id: raw_dict}``
        used to resolve the donor_name of customs. Built by the caller
        BEFORE transforming (otherwise donors can't be resolved).
    :param favorites: dict ``{mediaid: bool}`` loaded from
        ``paper_favorites.json`` (None = no known favorites).
    :param notes_keys: set of mediaids that have notes in
        ``paper_notes.json`` (None = no known notes).
    :param ledm_clc: dict ``{mediaid: {status, timestamp}}`` from the
        LEDM ``/Calibration/Calibration.xml`` (cf.
        ``calibration_ledm.parse_color_linearization``). If ``None``,
        ``_clc_status`` falls back to the SOAP <Calibration> block.
    """
    mediaid = raw["id"]
    # We derive factory/custom from the **MEDIAID format** rather than
    # from the XML <factory> attribute, which is wrong on some factory
    # papers added by a firmware update (returns <factory>0</…>).
    factory = not _is_custom_mediaid(mediaid)
    name = raw.get("name") or raw.get("short_name") or ""
    donor_id = raw.get("donor_id")
    donor_name = _resolve_donor(donor_id, factory_papers_by_id) if not factory else None

    caps = _capabilities_normalized(raw.get("capabilities") or {})
    ge_supported = caps["ge"]

    favorites = favorites or {}
    notes_keys = notes_keys or set()

    ledm_entry = (ledm_clc or {}).get(mediaid)

    return {
        "mediaid": mediaid,
        "name": name,
        "category": raw.get("category_name") or raw.get("category_id") or "",
        "factory": factory,
        "custom": not factory,
        "locked": bool(raw.get("is_locked")),
        "donor_id": donor_id if not factory else None,
        "donor_name": donor_name,
        "finish": _compute_finish(name, donor_name),
        "capabilities": caps,
        "inks": _inks_from_details(raw.get("details") or {}),
        "icc": _pick_icc_tickets(raw.get("profiles") or [], ge_supported),
        "clc": _clc_status(raw, ledm_entry=ledm_entry),
        "last_used": None,  # P1 fallback (cf. brief: null fallback acceptable)
        "favorite": bool(favorites.get(mediaid)),
        "has_notes": mediaid in notes_keys,
    }


def transform_all(
    raw_papers: list[dict],
    *,
    favorites: Optional[dict] = None,
    notes_keys: Optional[set] = None,
    ledm_clc: Optional[dict[str, dict]] = None,
) -> list[dict]:
    """Transform a full list of raw papers → list of Paper contracts,
    with donor resolution by internal lookup.

    First builds the factory index ``{id: raw}`` from ``raw_papers``,
    then iterates to transform each paper.

    :param ledm_clc: LEDM ``colorLinearization`` snapshot (cf.
        ``calibration_ledm.parse_color_linearization``). Passed as-is to
        each ``transform_paper`` to resolve the CLC status.
    """
    # Factory index for donor resolution — based on the MEDIAID format
    # (consistent with transform_paper).
    factory_papers_by_id = {
        p["id"]: p for p in raw_papers
        if not _is_custom_mediaid(p.get("id"))
    }
    return [
        transform_paper(
            p,
            factory_papers_by_id=factory_papers_by_id,
            favorites=favorites,
            notes_keys=notes_keys,
            ledm_clc=ledm_clc,
        )
        for p in raw_papers
    ]
