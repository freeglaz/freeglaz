import { useState } from 'react';
import { UIState } from '../../lib/state-machine.js';
import { ROLL_ECON_MARGIN_MM } from '../../lib/placement.js';
import EmptyDropzone from './EmptyDropzone.jsx';
import PaperPreview from './PaperPreview.jsx';

/**
 * Composes the viewer according to the UI state. Handles the visual drag-over and
 * delegates the drop to `onFileDrop`. Propagates the mouse drag callbacks to
 * PaperPreview.
 *
 * `advanced.positionX/Y` are **absolute** positions from the top-left
 * corner of the sheet. When null = auto-centered (computed
 * via `autoX` / `autoY` passed by App.jsx).
 *
 * @param {{
 *   state: string,
 *   file?: import('../../api/types').FileWithPreview | null,
 *   paper?: import('../../api/types').Paper | null,
 *   advanced?: any,
 *   autoX?: number, autoY?: number,
 *   onFileDrop: (f: File) => void,
 *   onPositionCommit?: (absX_mm: number, absY_mm: number) => void,
 *   onPositionReset?: () => void,
 * }} props
 */
export default function Viewer({
  state, file, paper, advanced, loading = false,
  autoX = 0, autoY = 0, centered = false,
  onFileDrop, onPositionCommit, onPositionReset,
}) {
  const [dragging, setDragging] = useState(false);
  // We completely disable accepting a new file
  // while a print is in progress on the Z9. No queue (cf. user
  // decision) — the user must wait for the print to finish before
  // loading a new document.
  // Patch 1 (25/05/2026): we also block during the upload→
  // info→preview pipeline (loading=true) to prevent an accidental re-drop that
  // would create noise on the backend side and confuse the user.
  const acceptingFiles = state !== UIState.E_PRINTING && !loading;

  const handleDragOver = (e) => {
    e.preventDefault();
    if (acceptingFiles) setDragging(true);
  };
  const handleDragLeave = () => setDragging(false);
  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (!acceptingFiles) return;  // E_PRINTING or loading → silent reject
    const f = e.dataTransfer.files?.[0];
    if (f) onFileDrop?.(f);
  };

  const showDropzone = state === UIState.A_EMPTY || state === UIState.D_NOPAPER;

  // Geometry from the backend if available, otherwise minimal fallback
  const g = file?.preview?.geometry;

  // Effective orientation: 90/270 transposes the footprint (w↔h) — the image
  // shown in the preview is rotated by PaperPreview accordingly.
  // The backend geometry is already computed on these transposed dims.
  const orientation = advanced?.orientation || 0;
  const oriented = orientation === 90 || orientation === 270;
  const effImgW = oriented ? file?.info?.height_mm : file?.info?.width_mm;
  const effImgH = oriented ? file?.info?.width_mm : file?.info?.height_mm;

  // Mouse drag. Enabled once the printable-zone bounds are known (the clamp
  // needs them), except while printing / with no paper. ROLL is now draggable
  // but on the HORIZONTAL axis only (dragAxis='x'): the roll width is fixed so
  // X has real room, whereas the length is free (sheet height derived from the
  // image) → vertical drag would be circular and is left out. SHEET keeps the
  // full XY drag.
  const isRoll = paper?.kind === 'roll';
  const hasPrintableBounds = Boolean(
    g && g.printable_w_mm > 0 && g.printable_h_mm > 0,
  );
  const draggable = Boolean(
    file && paper && hasPrintableBounds
      && state !== UIState.D_NOPAPER
      && state !== UIState.E_PRINTING,
  );
  const dragAxis = isRoll ? 'x' : 'xy';
  // The honest "centered" indicator is computed in App (single source, shared
  // with the "Center" button verdict, frozen while the geometry is stale) and
  // passed in as `centered`.

  // Optimistic position (Path A). Render the image at the FRONTEND-intended
  // position, NOT backend geometry.image_x_mm — which only refreshes after the
  // debounced /api/print/preview that queries the Z9 (~3 s). Center/rotation now
  // move the image instantly; the backend still owns the printable bounds / fits
  // (validation). The frontend auto anchor MATCHES the backend (sheet = centre ;
  // economical roll = 5 mm), so there is no jump when the backend confirms.
  const frontAutoX = isRoll
    ? ROLL_ECON_MARGIN_MM
    : ((paper?.width_mm ?? 0) - effImgW) / 2;
  const frontAutoY = isRoll
    ? ROLL_ECON_MARGIN_MM
    : ((paper?.height_mm ?? 0) - effImgH) / 2;
  const imageX = advanced?.positionX ?? frontAutoX;
  const imageY = advanced?.positionY ?? frontAutoY;

  return (
    <div
      className="flex-1 flex flex-col min-w-0"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}>
      {showDropzone && (
        <EmptyDropzone onPick={onFileDrop} dragging={dragging} loading={loading}/>
      )}
      {!showDropzone && file && paper && (
        <PaperPreview
          // AUTHORITATIVE sheet dimensions from the backend geometry
          // (compute_geometry). For an economical roll the backend
          // computes sheet_height_mm = effective top + image_h + 5mm (= image_h
          // +10mm without offset, grows with offset_y); status-side `paper.height_mm`
          // is null in ROLL → fallback to 9999 produced an
          // "infinite" vertical sheet, visually absurd. The backend
          // geometry is the source of truth, consistent with the lib
          // (CLI print-preview returns the same values).
          paperW_mm={g?.sheet_width_mm  || paper.width_mm}
          paperH_mm={g?.sheet_height_mm || paper.height_mm || 9999}
          imgW_mm={effImgW}
          imgH_mm={effImgH}
          orientation={orientation}
          dpi={file.info.dpi}
          filename={file.info.filename}
          file_id={file.info.id}
          image_x_mm={imageX}
          image_y_mm={imageY}
          printable_x_mm={g?.printable_x_mm}
          printable_y_mm={g?.printable_y_mm}
          printable_w_mm={g?.printable_w_mm}
          printable_h_mm={g?.printable_h_mm}
          overflow_mm={file.preview.overflow_mm}
          blocking_issues={file.preview.blocking_issues}
          centered={centered}
          oversize={state === UIState.C_OVERSIZED}
          position_x_mm={advanced?.positionX ?? autoX}
          position_y_mm={advanced?.positionY ?? autoY}
          draggable={draggable}
          dragAxis={dragAxis}
          onPositionCommit={onPositionCommit}
          onPositionReset={onPositionReset}/>
      )}
    </div>
  );
}
