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

"""Endpoints /api/files — upload + info + preview."""
import io
import logging
import shutil
from pathlib import Path

import pypdfium2 as pdfium
import pyvips
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from webapp.backend.models import FileInfo
from webapp.backend.services import file_storage
from webapp.backend.services.file_inspector import to_file_info
from webapp.backend.services.input_guard import (
    WebappInputRejected, validate_tiff_upload,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])

PREVIEW_MAX_SIDE = 1024


def _resolve_source(file_id: str) -> Path:
    src = file_storage.get_source(file_id)
    if src is None:
        raise HTTPException(status_code=404, detail="unknown file_id")
    return src


@router.post("")
async def upload(file: UploadFile = File(...)) -> dict:
    # Strict webapp gate: only a single-page RGB 8/16-bit TIFF with an embedded
    # ICC is accepted; the type is decided by content, not by the extension.
    # We stream to disk first, then validate, and delete the file on rejection
    # (nothing downstream ever sees a non-conforming upload). The CLI print path
    # does not go through here and is unaffected.
    name = file.filename or "upload"
    ext = Path(name).suffix.lower()
    file_id, dir_path = file_storage.new_storage()
    dest = dir_path / f"source{ext or '.tif'}"
    # Streaming in 1 MB chunks to avoid loading a large TIFF into RAM
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out, length=1024 * 1024)

    try:
        validate_tiff_upload(dest)
    except WebappInputRejected as e:
        shutil.rmtree(dir_path, ignore_errors=True)
        logger.info("Upload rejected %s: %s (%s)", name, e.code, e.message)
        raise HTTPException(415, detail={"code": e.code, "message": e.message})

    # Keep the original name to propagate it to the PJL JOB NAME at the moment
    # of the send. Without it, all jobs are named
    # "source.tif" in the Z9 queue and the user cannot tell them apart.
    file_storage.save_original_name(dir_path, name)
    logger.info("Upload %s → %s (%d bytes)", name, dest, dest.stat().st_size)
    return {"file_id": file_id, "filename": name}


@router.get("/{file_id}/info", response_model=FileInfo)
def info(file_id: str) -> FileInfo:
    src = _resolve_source(file_id)
    return to_file_info(file_id, src.name, src)


def _render_tiff_preview(path: Path) -> bytes:
    """Thumbnail a TIFF to a ≤1024 px 8-bit, color-managed PNG (libvips).

    ``pyvips.Image.thumbnail`` shrinks on load: large and 16-bit TIFFs are
    downscaled without decoding the full image into RAM — Pillow raised
    ``DecompressionBombError`` past ~179 Mpx (bug 3), which 500'd the endpoint
    and left the ``<img>`` with the broken-image icon. Orientation-agnostic:
    rendered in the native orientation; the print preview rotates in the UI.

    Colour: we keep the source ICC profile embedded in the PNG (no conversion,
    the pixels stay in their source space) so WebKit/ColorSync render it
    faithfully — like Preview.app — instead of the browser assuming sRGB and
    showing a wide-gamut file dull/desaturated. ``keep='icc'`` (libvips ≥ 8.15)
    drops the XMP/Photoshop bloat that broke huge scans (bug 3) while preserving
    the ICC in a single pass. The ICC is usually small (matrix profiles ≈0.5 KB)
    but a scanner/printer LUT profile can reach ~1 MB — still a valid ``iCCP``
    chunk that browsers render fine (verified in Chrome), unlike the oversized
    XMP text chunk of bug 3. On any failure (older libvips, missing/broken ICC)
    we fall back to the previous strip-all behaviour (raw pixels, untagged —
    dull but never broken).

    A TIFF stored as a single very large strip (e.g. cctiff device outputs, or an
    externally-made single-strip file) trips libvips' DoS memory ceiling on decode
    ("unable to copy to memory") — the ``thumbnail`` shrink-on-load cannot random-
    access such a file. The upload gate already validated the pixels, so on that
    failure we retry once with the ``[unlimited]`` loader option (memory ceiling
    removed for this one decode).
    """
    try:
        return _tiff_thumbnail_png(str(path))
    except pyvips.Error as e:
        logger.info("Preview: %s tripped the libvips memory limit — retrying "
                    "[unlimited] (%s).", path.name, str(e).splitlines()[0])
        return _tiff_thumbnail_png(str(path) + "[unlimited]")


def _tiff_thumbnail_png(loadpath: str) -> bytes:
    """Thumbnail → materialise → PNG. ``loadpath`` may carry libvips loader options
    (e.g. ``file.tif[unlimited]``)."""
    img = pyvips.Image.thumbnail(loadpath, PREVIEW_MAX_SIDE)
    # ``thumbnail`` already yields 8-bit; cast defensively for any non-uchar
    # input (e.g. a float TIFF). ``shift`` scales 16-bit down, not truncates.
    # Neither the cast (depth) nor the flatten (alpha) changes the colour space,
    # so the source ICC stays valid.
    if img.format != "uchar":
        img = img.cast("uchar", shift=True)
    # Composite alpha onto white — matches the previous Pillow RGB behavior.
    if img.hasalpha():
        img = img.flatten(background=255)
    # Materialize the (small ≤1024 px) thumbnail so the fallback re-write cannot
    # hit the sequential-source "out of order read".
    img = img.copy_memory()
    try:
        # keep='icc' strips everything EXCEPT the ICC (XMP/EXIF/Photoshop gone).
        return img.write_to_buffer(".png", keep="icc")
    except Exception as exc:  # noqa: BLE001 — never worse than before
        logger.debug("Preview ICC-keep unavailable (%s: %s) — stripping all.",
                     type(exc).__name__, exc)
        return img.write_to_buffer(".png", strip=True)


def _render_pdf_preview(path: Path) -> bytes:
    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[0]
        w_pt, h_pt = page.get_size()
        scale = PREVIEW_MAX_SIDE / max(w_pt, h_pt)
        img = page.render(scale=scale).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()


@router.get("/{file_id}/preview")
def preview(file_id: str) -> Response:
    src = _resolve_source(file_id)
    ext = src.suffix.lower()
    try:
        if ext in (".tif", ".tiff"):
            data = _render_tiff_preview(src)
        elif ext == ".pdf":
            data = _render_pdf_preview(src)
        else:
            raise HTTPException(
                status_code=415,
                detail={"code": "preview_unsupported_format",
                        "message": f"Preview not supported for {ext}"},
            )
    except HTTPException:
        raise
    except Exception as e:
        # Never surface a raw 500 to the <img>. The upload gate already
        # validated the TIFF, so a failure here means the pixel data is
        # unreadable/corrupt (or truncated). Name that cause — the frontend
        # shows a message instead of the broken-image "?" icon.
        logger.warning("Preview render failed for %s: %s", src.name, e)
        raise HTTPException(
            status_code=422,
            detail={"code": "preview_render_failed",
                    "message": "The image could not be decoded for preview "
                               "(unreadable or corrupt file)."},
        )
    return Response(content=data, media_type="image/png")
