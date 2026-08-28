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

from lib.z9_client import devicelink, tiffgamut
from lib.z9_client.argyll import ArgyllNotFound
from lib.z9_client.exceptions import Z9Error
from lib.z9_client.inspect import HpProprietaryDecoder, analyze_trc
from lib.z9_client.printing import fetch_resident_icc
from webapp.backend.routes.status import build_loaded_paper, get_z9
from webapp.backend.services import file_storage, luminance_priority

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/convert", tags=["convert"])

# collink GAMUT INTENTS (characterised on the Canson témoin; see the campaign
# reports). Vocabulary: "gamut intent" / "intent", NOT "strategy" (that word is
# lib.z9_client.strategies = colprof profiling presets, an unrelated notion). The
# four NATIVE intents are collink gamut-mapping intents (collink -G -i<intent>);
# "luminance_priority" is the freeglaz custom radial-chroma abstract (collink -s
# -ir -p, τ-controlled — NOT a -G intent). DEFAULT = "relative" == the previous
# Convert behaviour (-G -ir) → strictly non-regressive.
_NATIVE_INTENT = {
    "relative": "r",               # -G -ir : white-point matched (current default)
    "luminance_matched": "la",     # -G -ila : deep-shadow separation; lightens saturates
    "perceptual": "p",             # -G -ip  : 3-D perceptual compression
    "luminance_preserving": "lp",  # -G -ilp : lightness-preserving perceptual
}
_CUSTOM_INTENT = "luminance_priority"   # -s -ir -p <abstract τ> : preserves saturated luminance, desaturates


class ConvertBody(BaseModel):
    file_id: str = Field(..., min_length=1)
    gamut_intent: str = "relative"     # relative | luminance_matched | perceptual |
                                       # luminance_preserving | luminance_priority
    tau: float = Field(1.0, ge=luminance_priority.TAU_MIN, le=luminance_priority.TAU_MAX)
                                       # luminance_priority only: 0.5 (protect luminance) → 2.0 (keep chroma)
    quality: str = "h"                 # l | m | h | u
    gloss_enhancer: str                # GE state selecting the paper's resident (same vocab as print)
    image_aware: bool = False          # image-aware axis (native -G strategies only): map the image's
                                       # OWN occupied gamut (tiffgamut) instead of the full source gamut
    dest_viewcond: str = "default"     # destination CIECAM02 viewing conditions (collink -d, native only):
                                       # default | pp | pc | pe | pm (print presets only)


# Convert v1 contract (documented, not accidental): the Argyll engine supports
# mft1/mft2 LUTs (incl. under a v4 header — Z9-native and freeglaz-built) and
# does NOT support mAB/mBA (true v4 lutAToBType, i1Profiler output). We refuse
# mAB/mBA CLEANLY here, BEFORE any Argyll call, so no cryptic Argyll error leaks.
# This gate is Convert-scoped only — it never touches the Print path.
def _reject_unsupported_lut(icc: bytes, which: str) -> None:
    status = devicelink.convert_lut_support(icc)
    if status in ("UNSUPPORTED_MAB_MBA", "NO_USABLE_TRANSFORM"):
        raise HTTPException(
            422,
            detail={"code": "unsupported_lut", "which": which, "status": status,
                    "message": "Profile not supported by the Convert module. "
                               "This profile uses ICC mAB/mBA tables, which the Argyll "
                               "conversion engine currently used by freeglaz does not "
                               "support. Convert accepts profiles using mft1/mft2 tables."},
        )


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
    # 0. Gamut intent must be one of the five characterised intents (clean 422).
    if body.gamut_intent not in _NATIVE_INTENT and body.gamut_intent != _CUSTOM_INTENT:
        raise HTTPException(
            422, detail={"code": "unknown_gamut_intent", "gamut_intent": body.gamut_intent,
                         "message": f"unknown gamut intent {body.gamut_intent!r}; "
                                    f"expected {sorted([*_NATIVE_INTENT, _CUSTOM_INTENT])}"})

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
    _reject_unsupported_lut(src_icc, "source")   # clean refusal BEFORE any Argyll call

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
    _reject_unsupported_lut(dest_icc, "destination")   # clean refusal BEFORE any Argyll call

    # 3. Build the DeviceLink per gamut intent + apply (cctiff) → device TIFF on disk.
    #    Traceability (doctrine): the gamut intent (+ τ for the custom one) is encoded
    #    in the output filename so a job is identifiable a posteriori.
    storage_dir = file_storage.get_storage(body.file_id)
    is_custom = body.gamut_intent == _CUSTOM_INTENT
    if is_custom:
        # -s mode: image-aware (-G <gam>) and dest viewing conditions (-d) are
        # gamut-mapping-mode features → they do NOT apply here (ignored).
        tag = f"_tau{body.tau}"; ia_tag = vc_tag = ""
    else:
        tag = ""
        ia_tag = "_ia" if body.image_aware else ""
        vc_tag = f"_{body.dest_viewcond}" if body.dest_viewcond not in ("default", "", None) else ""
    dev_tiff = storage_dir / f"converted_{body.gamut_intent}{tag}_{body.quality}{ia_tag}{vc_tag}.tif"
    with tempfile.TemporaryDirectory(prefix="freeglaz_convert_") as tmp:
        tmp = Path(tmp)
        # Normalize both profiles before collink: strip the v4 mluc TEXT tags
        # (collink crashes copying them) + relabel the header v2.4. Colorimetry
        # is preserved untouched (mft1/mft2 LUTs copied byte-for-byte; mAB/mBA
        # were already refused above by _reject_unsupported_lut).
        (tmp / "source.icc").write_bytes(devicelink.normalize_icc_for_argyll(src_icc))
        (tmp / "dest.icc").write_bytes(devicelink.normalize_icc_for_argyll(dest_icc))
        # Tag the device output with the loaded paper's profile so it opens
        # colour-managed in an editor. We embed the ORIGINAL resident (full
        # metadata/name). Pure assignment: cctiff -e leaves device pixels intact.
        (tmp / "paper.icc").write_bytes(dest_icc)
        try:
            if is_custom:
                # Custom "luminance priority": radial-chroma abstract at τ inserted
                # via collink -s -ir -p (ported bench; preserves saturated luminance,
                # desaturates). Intra-profile neutral guard before applying.
                abstract = luminance_priority.build_luminance_priority_abstract(
                    tmp / "dest.icc", body.tau)
                luminance_priority.assert_neutral_abstract(abstract)
                luminance_priority.build_link(
                    tmp / "source.icc", tmp / "dest.icc", tmp / "link.icc", abstract)
            else:
                # Native gamut-mapping intent (collink -G -i<intent>). Image-aware
                # restricts the mapping to the gamut the image actually occupies.
                image_gam = None
                if body.image_aware:
                    image_gam = tmp / "image.gam"
                    tiffgamut.run_tiffgamut(tmp / "source.icc", src_tiff, image_gam)
                devicelink.run_collink(
                    tmp / "source.icc", tmp / "dest.icc", tmp / "link.icc",
                    intent=_NATIVE_INTENT[body.gamut_intent], quality=body.quality,
                    image_gam=image_gam, dest_viewcond=body.dest_viewcond)
            # Uncompressed output: cctiff's LZW default is a single huge strip
            # that the Print path (tifffile) and the preview (libvips) cannot read
            # in this environment, and it expands noisy 16-bit data anyway. Pixels
            # are identical; the device file is just uncompressed (often smaller).
            devicelink.apply_cctiff(tmp / "link.icc", src_tiff, dev_tiff,
                                    embed_icc=tmp / "paper.icc", uncompressed=True)
        except ArgyllNotFound as e:
            raise HTTPException(
                503,
                detail={"code": "argyll_conversion_missing",
                        "message": f"DeviceLink conversion needs collink/cctiff: {e}"})
        except (ValueError, RuntimeError) as e:
            raise HTTPException(500, detail=f"conversion failed: {e}")

    # Full command in the job trace (doctrine: total traceability). Profile args
    # are the ephemeral normalized copies (shown as source/dest/link.icc); the paper
    # id + GE identify the live resident that was fetched.
    if is_custom:
        full_cmd = (f"collink -v -qh -s -ir -p <abstract τ={body.tau}> "
                    "source.icc dest.icc link.icc")
    else:
        gam = " <image.gam>" if body.image_aware else ""
        vc = (f" -d {body.dest_viewcond}"
              if body.dest_viewcond not in ("default", "", None) else "")
        full_cmd = (f"collink -v -q{body.quality} -G{gam} "
                    f"-i{_NATIVE_INTENT[body.gamut_intent]}{vc} source.icc dest.icc link.icc")
    logger.info("Convert: %s → device | gamut_intent=%s | %s | cctiff -e paper.icc "
                "(GE=%s, paper=%s, out=%s)",
                body.file_id, body.gamut_intent, full_cmd,
                body.gloss_enhancer, loaded.id, dev_tiff.name)
    return FileResponse(
        str(dev_tiff), media_type="image/tiff",
        filename=f"converted_{body.gamut_intent}{tag}{ia_tag}{vc_tag}.tif",
    )
