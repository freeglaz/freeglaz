# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert routes — DeviceLink conversion (JALON 1 socle).

Upstream, separate, bypassable stage: converts a dropped image (source space,
read from its embedded ICC) toward the loaded paper's device via an Argyll
DeviceLink (``collink -G`` + ``cctiff``), and writes a device TIFF to disk.

- SOURCE profile = the image's EMBEDDED ICC (detection, not a frozen working
  space). Absent → the conversion is refused (never assume a default space).
- DEST profile = the LOADED paper's resident characterization, resolved by the
  SAME primitive the print path uses (``fetch_resident_icc(paper, GE)``). No
  selector, no user-chosen dest.
- The PRINT module is untouched: this only produces a device file on disk.
"""
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from lib.z9_client import devicelink
from lib.z9_client.argyll import ArgyllNotFound
from lib.z9_client.exceptions import Z9Error
from lib.z9_client.inspect import HpProprietaryDecoder, analyze_trc
from lib.z9_client.printing import fetch_resident_icc
from webapp.backend.routes.status import build_loaded_paper, get_z9
from webapp.backend.services import file_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/convert", tags=["convert"])


class ConvertBody(BaseModel):
    file_id: str = Field(..., min_length=1)
    intent: str = "r"                  # collink -i choice: r | p | lp (→ -ir/-ip/-ilp)
    quality: str = "h"                 # l | m | h | u
    gloss_enhancer: str                # GE state selecting the paper's resident (same vocab as print)


def _source_profile_summary(icc_bytes: bytes) -> dict:
    """Space + TRC summary of an embedded profile (assembles existing bricks)."""
    header = HpProprietaryDecoder.extract_header(icc_bytes)
    tags = HpProprietaryDecoder.parse_icc_tags(icc_bytes)
    trc = analyze_trc(icc_bytes, tags)
    return {
        "has_profile": True,
        "color_space": header.get("color_space"),
        "pcs": header.get("pcs"),
        "trc": trc,
    }


@router.get("/source-info")
def convert_source_info(file_id: str = Query(..., min_length=1)) -> dict:
    """Detected space + TRC of the dropped image's embedded ICC (Étape 2).

    ``has_profile=false`` if the image carries no embedded profile → the UI must
    tell the user the source cannot be converted (detection, not figeage).
    """
    src = file_storage.get_source(file_id)
    if src is None:
        raise HTTPException(404, detail="file not found")
    icc = devicelink.extract_embedded_icc(src)
    if icc is None:
        return {"has_profile": False}
    return _source_profile_summary(icc)


@router.post("")
def convert(body: ConvertBody, request: Request,
            z9=Depends(get_z9)) -> FileResponse:
    """Convert the dropped image to the loaded paper's device via a DeviceLink,
    write the device TIFF to disk, and return it as a download.

    Errors: 404 (file), 400 (no source profile), 409 (no paper loaded),
    503 (collink/cctiff not installed), 500 (conversion failed).
    """
    # 1. Source TIFF + its embedded profile (SOURCE)
    src_tiff = file_storage.get_source(body.file_id)
    if src_tiff is None:
        raise HTTPException(404, detail="file not found")
    src_icc = devicelink.extract_embedded_icc(src_tiff)
    if src_icc is None:
        raise HTTPException(
            400,
            detail={"code": "no_source_profile",
                    "message": "The image carries no embedded ICC profile — "
                               "cannot convert (no source space to read)."},
        )

    # 2. DEST = loaded paper's resident, via the SAME primitive as print
    if z9 is None:
        raise HTTPException(409, detail="Z9 not configured")
    try:
        dashboard = z9.device.status()
        loaded = build_loaded_paper(z9, dashboard, request.app.state.capabilities_cache)
    except Z9Error as e:
        raise HTTPException(502, detail=f"Z9 unreachable: {e}")
    if loaded is None:
        raise HTTPException(409, detail="No paper loaded in the Z9")
    try:
        dest_icc = fetch_resident_icc(z9, loaded.id, body.gloss_enhancer)
    except Z9Error as e:
        raise HTTPException(502, detail=f"cannot read the loaded paper resident: {e}")

    # 3. DeviceLink (collink -G) + apply (cctiff) → device TIFF on disk
    storage_dir = file_storage.get_storage(body.file_id)
    dev_tiff = storage_dir / f"converted_{body.intent}_{body.quality}.tif"
    with tempfile.TemporaryDirectory(prefix="freeglaz_convert_") as tmp:
        tmp = Path(tmp)
        (tmp / "source.icc").write_bytes(src_icc)
        (tmp / "dest.icc").write_bytes(dest_icc)
        try:
            devicelink.run_collink(
                tmp / "source.icc", tmp / "dest.icc", tmp / "link.icc",
                intent=body.intent, quality=body.quality)
            devicelink.apply_cctiff(tmp / "link.icc", src_tiff, dev_tiff)
        except ArgyllNotFound as e:
            raise HTTPException(
                503,
                detail={"code": "argyll_conversion_missing",
                        "message": f"DeviceLink conversion needs collink/cctiff: {e}"})
        except (ValueError, RuntimeError) as e:
            raise HTTPException(500, detail=f"conversion failed: {e}")

    logger.info("Convert: %s → device via -G -i%s -q%s (GE=%s, paper=%s)",
                body.file_id, body.intent, body.quality, body.gloss_enhancer, loaded.id)
    return FileResponse(
        str(dev_tiff), media_type="image/tiff",
        filename=f"converted_{body.intent}.tif",
    )
