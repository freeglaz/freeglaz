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

"""3D gamut extraction endpoints (simplified).

- GET /api/profiles/gamut?path=...&intent=relative|perceptual|saturation
  Single method device_surface_grid (RGB only, 400 on CMYK).
- GET /api/profiles/gamt?path=...
  Iso-surface of the gamt tag (404 if absent).
- GET /api/gamut/reference/{name}?intent=...
- GET /api/profiles/lut_scatter?path=...&intent=...&resolution=9|17

All synchronous (extraction < 1 s in practice, disk cache active).
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from lib.z9_client import Z9Error
from lib.z9_client.gamut import (
    REFERENCE_NAMES,
    extract_clut_scatter,
    extract_gamt_mesh_cached,
    extract_gamut_mesh_cached,
    extract_reference_mesh,
    intent_for_lut_tag,
)
from webapp.backend.services import settings_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["gamut"])


_MEDIAID_REGEX = r"^[A-F0-9]{32}$"
_SLOT_VALUES = {"on", "off", "single", "ge_on", "ge_off"}


def _normalize_slot(slot: str) -> str:
    return {"ge_on": "on", "ge_off": "off"}.get(slot, slot)


def _soap_gloss(slot: str) -> str:
    return {"on": "FULLPAGE", "off": "OFF", "single": "OFF"}.get(slot, "OFF")


def _resolve_profile_path(
    request: Request,
    path: Optional[str],
    paper_mediaid: Optional[str],
    slot: Optional[str],
) -> tuple:
    """Returns (path, temp_to_cleanup) depending on source. Firmware mode
    downloads the ICC into a temporary file."""
    import tempfile
    if path and paper_mediaid:
        raise HTTPException(422, detail="'path' and 'paper_mediaid' are mutually exclusive")
    if not path and not paper_mediaid:
        raise HTTPException(422, detail="Provide 'path' or ('paper_mediaid' + 'slot')")
    if path:
        p = Path(path)
        if not p.is_absolute():
            raise HTTPException(422, detail="'path' must be absolute")
        if not p.exists() or not p.is_file():
            raise HTTPException(404, detail=f"File not found: {p}")
        return str(p), None
    # Firmware fetch
    if not slot:
        raise HTTPException(422, detail="'slot' required with 'paper_mediaid'")
    normalized = _normalize_slot(slot)
    if normalized not in _SLOT_VALUES:
        raise HTTPException(422, detail=f"'slot' must be one of {sorted(_SLOT_VALUES)}")
    z9 = getattr(request.app.state, "z9", None)
    if z9 is None:
        raise HTTPException(503, detail="Z9Client not configured")
    soap_ge = _soap_gloss(normalized)
    with tempfile.NamedTemporaryFile(suffix=".icc", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        z9.paper.export_icc(
            ref=paper_mediaid,
            output_path=str(tmp_path),
            gloss_enhancer=soap_ge,
            quality="BEST",
            color_space="RGB",
        )
    except Z9Error as e:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise HTTPException(404, detail=f"Firmware ICC fetch failed: {e}")
    return str(tmp_path), tmp_path


_INTENTS = {"perceptual", "relative", "saturation"}


@router.get("/profiles/gamut")
def get_profile_gamut(
    request: Request,
    path: Optional[str] = Query(None),
    paper_mediaid: Optional[str] = Query(None, pattern=_MEDIAID_REGEX),
    slot: Optional[str] = Query(None),
    intent: str = Query("relative"),
) -> dict:
    """Extract the mesh of the effective gamut of an RGB profile.

    Single method device_surface_grid (6 RGB device faces × 17² +
    A2B, grid triangulation, grid wireframe). Raises 400 if the
    profile is not RGB.
    """
    if intent not in _INTENTS:
        raise HTTPException(422, detail=f"'intent' must be one of {sorted(_INTENTS)}")
    profile_path, to_cleanup = _resolve_profile_path(request, path, paper_mediaid, slot)
    try:
        return extract_gamut_mesh_cached(profile_path, intent=intent)
    except ValueError as e:
        # Non-RGB profile (CMYK or other)
        raise HTTPException(400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("Gamut extraction failed")
        raise HTTPException(500, detail=f"Gamut extraction failed: {e}")
    finally:
        if to_cleanup:
            try:
                to_cleanup.unlink()
            except OSError:
                pass


@router.get("/profiles/gamt")
def get_profile_gamt(
    request: Request,
    path: Optional[str] = Query(None),
    paper_mediaid: Optional[str] = Query(None, pattern=_MEDIAID_REGEX),
    slot: Optional[str] = Query(None),
) -> dict:
    """Extract the iso-surface of the gamt tag (boundary declared by the
    profiler). 404 if the profile has no gamt tag."""
    profile_path, to_cleanup = _resolve_profile_path(request, path, paper_mediaid, slot)
    try:
        return extract_gamt_mesh_cached(profile_path)
    except FileNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except ValueError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("Gamt extraction failed")
        raise HTTPException(500, detail=f"Gamt extraction failed: {e}")
    finally:
        if to_cleanup:
            try:
                to_cleanup.unlink()
            except OSError:
                pass


@router.get("/gamut/reference/{name}")
def get_gamut_reference(
    name: str,
    intent: str = Query("relative"),
) -> dict:
    """Return the mesh of a standard reference (sRGB, AdobeRGB, ...)
    extracted via device_surface_grid (single method)."""
    if name not in REFERENCE_NAMES and name != "none":
        raise HTTPException(404, detail=f"Unknown reference: {name}. "
                            f"Available: {REFERENCE_NAMES}")
    if name == "none":
        return {"vertices": [], "indices": [], "colors_srgb": [],
                "n_vertices": 0, "n_triangles": 0, "volume_lab": 0.0,
                "reference_name": "none"}
    if intent not in _INTENTS:
        raise HTTPException(422, detail=f"'intent' must be one of {sorted(_INTENTS)}")
    try:
        return extract_reference_mesh(name, intent=intent)
    except FileNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("Reference extraction failed")
        raise HTTPException(500, detail=f"Reference extraction failed: {e}")


@router.get("/profiles/lut_scatter")
def get_lut_scatter(
    request: Request,
    path: Optional[str] = Query(None),
    paper_mediaid: Optional[str] = Query(None, pattern=_MEDIAID_REGEX),
    slot: Optional[str] = Query(None),
    tag: Optional[str] = Query(None, description="LUT tag signature (A2B0...)"),
    intent: Optional[str] = Query(None,
                                  pattern="^(perceptual|relative|saturation|absolute)$"),
    resolution: Optional[int] = Query(None, ge=5, le=33),
) -> dict:
    """3D scatter for the mini-render in a LUT popover.

    If ``tag`` is provided, the intent is inferred (A2B0/B2A0 → perceptual,
    A2B1/B2A1 → relative, A2B2/B2A2 → saturation). Otherwise uses
    ``intent`` or perceptual by default.

    Resolution read from Settings ``gamut.lut_scatter_resolution``
    unless provided explicitly.
    """
    resolved_intent = (
        intent
        or (intent_for_lut_tag(tag) if tag else "perceptual")
    )
    if resolution is None:
        setting_val = settings_store.get("gamut.lut_scatter_resolution") or "9"
        resolution = int(setting_val)
    profile_path, to_cleanup = _resolve_profile_path(request, path, paper_mediaid, slot)
    try:
        return extract_clut_scatter(
            profile_path, intent=resolved_intent, resolution=resolution,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("CLUT scatter extraction failed")
        raise HTTPException(500, detail=f"CLUT scatter failed: {e}")
    finally:
        if to_cleanup:
            try:
                to_cleanup.unlink()
            except OSError:
                pass
