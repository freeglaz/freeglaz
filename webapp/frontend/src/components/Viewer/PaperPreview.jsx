import { useEffect, useRef, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getFilePreviewUrl } from '../../api/client.js';

/**
 * Geometric preview of the paper + image inside it.
 *
 * Computes a common `ppm` (pixels-per-millimeter) for the zone, then draws:
 *   - The paper rectangle (off-white + strong shadow)
 *   - The printable zone dashed (informative)
 *   - The image positioned according to image_x_mm/image_y_mm (backend), shadowed
 *     by dragDelta during the mouse drag
 *   - If oversize: red hatching over the whole image + top-right badge
 *     with directional detail
 *   - Crop marks at the 4 corners of the paper
 *   - Filename pill top-left
 *   - Info line below the preview
 *
 * Mouse drag — "local position dominates" pattern:
 *   - ``localPos`` (absolute mm) shadows image_x_mm/image_y_mm during and
 *     after a drag, until backend confirmation. A single source of
 *     truth for what is displayed → consistent with the clamp (both
 *     derive from localPos), no divergence between render and clamp even
 *     in fast double-drag.
 *   - mouseMove: localPos = startPos + mouse_delta, clamped in absolute.
 *   - mouseUp: commit the final absolute position to the global state
 *     (`onPositionCommit(finalPos.x, finalPos.y)`). No more delta
 *     computation (B13 resolved) — the absolute is passed as-is. localPos is
 *     KEPT → image stays at finalPos during the ~300 ms of debounce
 *     + backend fetch, no visual jump. localPos is released by a
 *     useEffect when the backend returns the new image_x_mm (= our
 *     commit was applied, or the user edited an input in the
 *     sidebar in the meantime — in that case the input wins).
 *   - Clamp within the printable zone (unless image > zone: free drag).
 *   - Tooltip shows the final absolute position (consistent with
 *     Position X / Position Y in the sidebar).
 *   - Gray crosshair visible at sheet center during the drag.
 *   - Double-click → onPositionReset() (App sets positionX=positionY=null).
 *
 * @param {{
 *   paperW_mm: number, paperH_mm: number,
 *   imgW_mm: number, imgH_mm: number,
 *   dpi: number, filename?: string, file_id?: string,
 *   image_x_mm?: number, image_y_mm?: number,
 *   printable_x_mm?: number, printable_y_mm?: number,
 *   printable_w_mm?: number, printable_h_mm?: number,
 *   overflow_mm?: { left: number, top: number, right: number, bottom: number },
 *   blocking_issues?: string[],
 *   centered?: boolean,
 *   oversize?: boolean,
 *   position_x_mm?: number, position_y_mm?: number,
 *   draggable?: boolean,
 *   dragAxis?: 'xy' | 'x',
 *   onPositionCommit?: (absX_mm: number, absY_mm: number, axis?: 'xy'|'x') => void,
 *   onPositionReset?: () => void,
 *   maxW?: number, maxH?: number,
 * }} props
 *
 * `dragAxis` constrains the mouse drag: `'xy'` (sheet, default) frees both
 * axes; `'x'` (roll) locks Y and commits X only — App keeps Y on auto, so the
 * free-length roll never freezes a meaningless vertical position.
 */
export default function PaperPreview({
  paperW_mm, paperH_mm, imgW_mm, imgH_mm, orientation = 0, dpi, filename, file_id,
  image_x_mm, image_y_mm,
  printable_x_mm, printable_y_mm, printable_w_mm, printable_h_mm,
  overflow_mm,
  blocking_issues = [],
  centered = false,
  oversize = false,
  position_x_mm = 0, position_y_mm = 0,
  draggable = false,
  dragAxis = 'xy',
  onPositionCommit, onPositionReset,
  maxW = 720, maxH = 540,
}) {
  const { t } = useTranslation();
  // ppm: we align on the sheet + any potential image overflow
  const ix_mm = image_x_mm ?? ((paperW_mm - imgW_mm) / 2);
  const iy_mm = image_y_mm ?? ((paperH_mm - imgH_mm) / 2);
  const refW = Math.max(paperW_mm, ix_mm + imgW_mm, -ix_mm + imgW_mm);
  const refH = Math.max(paperH_mm, iy_mm + imgH_mm, -iy_mm + imgH_mm);
  const ppm = Math.min(maxW / refW, maxH / refH);

  const pw = paperW_mm * ppm, ph = paperH_mm * ppm;
  const iw = imgW_mm * ppm,   ih = imgH_mm * ppm;

  // Visual rotation of the image within its footprint. The container is iw×ih
  // (= EFFECTIVE footprint, already transposed for 90/270). We size the
  // <img> to the NATIVE footprint (nw×nh), center it, then rotate it:
  // after rotation it fills the container exactly without deformation
  // (the native aspect is preserved → objectFit cover does not crop).
  const rotatedPreview = orientation === 90 || orientation === 270;
  const imgNativeW = rotatedPreview ? ih : iw;
  const imgNativeH = rotatedPreview ? iw : ih;

  // Printable zone (if provided). Fallback: none (nothing to draw).
  const hasPrintable = printable_w_mm > 0 && printable_h_mm > 0;
  const prx = (printable_x_mm ?? 0) * ppm;
  const pry = (printable_y_mm ?? 0) * ppm;
  const prw = (printable_w_mm ?? 0) * ppm;
  const prh = (printable_h_mm ?? 0) * ppm;

  // ── Mouse drag ─────────────────────────────────────────────────────
  // localPos = absolute position (mm) of the image during the drag and just
  // after its commit, until backend confirmation. When localPos is
  // non-null, it dominates over ix_mm/iy_mm — a single source of truth,
  // no divergence between render, clamp and tooltip.
  const [localPos,  setLocalPos]  = useState(null);   // { x, y } mm | null
  const [dragging,  setDragging]  = useState(false);
  const [cursorPos, setCursorPos] = useState({ x: 0, y: 0 });
  // Preview PNG failed to load (backend 422 preview_render_failed: unreadable /
  // corrupt file). We then show a named-cause message instead of the browser's
  // broken-image "?" icon. Reset on a new file_id (retry the fresh image).
  const [imgError, setImgError] = useState(false);
  useEffect(() => { setImgError(false); }, [file_id]);

  // ppm + bounds in refs: used by handleMouseDown to take
  // a snapshot AT mouseDown. Mutated on each render to stay fresh.
  // During the drag, we NEVER read them — the local closure holds its
  // own snapshots, which isolates each drag from the others (double
  // mouseDown without intermediate mouseUp case).
  const boundsRef = useRef(null);
  const ppmRef    = useRef(1);
  boundsRef.current = {
    minX: printable_x_mm ?? 0,
    minY: printable_y_mm ?? 0,
    maxX: (printable_x_mm ?? 0) + (printable_w_mm ?? paperW_mm) - imgW_mm,
    maxY: (printable_y_mm ?? 0) + (printable_h_mm ?? paperH_mm) - imgH_mm,
  };
  ppmRef.current = ppm;

  // Displayed position — localPos dominates over ix_mm/iy_mm when present
  const display_x_mm = localPos ? localPos.x : ix_mm;
  const display_y_mm = localPos ? localPos.y : iy_mm;
  const display_ix   = display_x_mm * ppm;
  const display_iy   = display_y_mm * ppm;

  function handleMouseDown(e) {
    if (!draggable) return;
    if (dragging) return;  // idempotent: refuses 2nd mouseDown in parallel
    e.preventDefault();

    // LOCAL snapshot (closure), not via a shared ref. Each call to
    // handleMouseDown creates a NEW closure with its own ``snap``
    // captured by onMove/onUp. If a 2nd mouseDown occurred before
    // the previous one's mouseUp (extreme case with a missed event), it would have
    // its own closure without interfering with the first. But the
    // ``if (dragging) return`` above short-circuits this situation
    // anyway.
    const snap = {
      mouseX: e.clientX, mouseY: e.clientY,
      startX: display_x_mm,
      startY: display_y_mm,
      ppm:    ppmRef.current,
      bounds: { ...boundsRef.current },
      axis:   dragAxis,   // 'x' (roll) locks Y; 'xy' (sheet) frees both
    };
    setDragging(true);
    setCursorPos({ x: e.clientX, y: e.clientY });
    document.body.style.userSelect = 'none';

    const clampSnap = (x, y) => {
      const b = snap.bounds;
      return {
        x: b.maxX >= b.minX ? Math.max(b.minX, Math.min(b.maxX, x)) : x,
        y: b.maxY >= b.minY ? Math.max(b.minY, Math.min(b.maxY, y)) : y,
      };
    };

    const onMove = (ev) => {
      const dx = (ev.clientX - snap.mouseX) / snap.ppm;
      // Roll (axis='x'): Y is pinned to its start → horizontal drag only.
      const dy = snap.axis === 'x' ? 0 : (ev.clientY - snap.mouseY) / snap.ppm;
      setLocalPos(clampSnap(snap.startX + dx, snap.startY + dy));
      setCursorPos({ x: ev.clientX, y: ev.clientY });
    };
    const onUp = (ev) => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = '';
      const dx = (ev.clientX - snap.mouseX) / snap.ppm;
      const dy = snap.axis === 'x' ? 0 : (ev.clientY - snap.mouseY) / snap.ppm;
      const finalPos = clampSnap(snap.startX + dx, snap.startY + dy);
      setDragging(false);
      const moved = Math.abs(finalPos.x - snap.startX) > 0.01
                 || Math.abs(finalPos.y - snap.startY) > 0.01;
      if (moved) {
        setLocalPos(finalPos);
        // Bug B13 resolved: we commit the ABSOLUTE position directly
        // (consistent with native Z9 PJL). No more delta computation —
        // the old formula (offset_x_mm + finalPos.x - ix_mm) accumulated
        // a delta on a delta and exposed the user to values
        // relative to centering, a mental model incompatible with the
        // graphic standards.
        // axis passed through: 'x' → App commits X only (Y stays auto).
        onPositionCommit?.(finalPos.x, finalPos.y, snap.axis);
      } else {
        setLocalPos(null);
      }
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  function handleDoubleClick() {
    if (!draggable) return;
    onPositionReset?.();
  }

  // Releases localPos when the backend returns a new ix_mm/iy_mm
  // (= our commit was applied, OR the user edited an input
  // in the sidebar in the meantime — in that case the input wins, expected
  // behavior). Unconditional release: the tolerance previously used
  // blocked the release in the Center-after-drag case (ix_mm = center,
  // localPos = drag-position, diff > tolerance → UX blockage).
  //
  // ``dragging`` deliberately EXCLUDED from the deps: we want the trigger on
  // ix_mm/iy_mm, not on the mouseUp's true→false transition (which
  // would bring the image back to its pre-drag position during the ~300 ms of
  // backend debounce, an ugly visual jump). The ``if (!dragging)``
  // protects against a fetch that would arrive during a new drag.
  useEffect(() => {
    if (!dragging) setLocalPos(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ix_mm, iy_mm]);

  // Drag tooltip: final absolute position (mm from the top-left corner
  // of the sheet). Bug B13 — we now show the
  // ABSOLUTE position, consistent with the Position X / Position Y inputs of the
  // sidebar, rather than the internal delta computation.
  const finalPosX = localPos ? localPos.x : ix_mm;
  const finalPosY = localPos ? localPos.y : iy_mm;

  // Directional detail for the oversize badge
  const overflowParts = overflow_mm ? [
    overflow_mm.left   > 0 ? t('print.preview_overflow_left',   { mm: overflow_mm.left.toFixed(0) })   : null,
    overflow_mm.top    > 0 ? t('print.preview_overflow_top',    { mm: overflow_mm.top.toFixed(0) })    : null,
    overflow_mm.right  > 0 ? t('print.preview_overflow_right',  { mm: overflow_mm.right.toFixed(0) })  : null,
    overflow_mm.bottom > 0 ? t('print.preview_overflow_bottom', { mm: overflow_mm.bottom.toFixed(0) }) : null,
  ].filter(Boolean) : [];

  const cursorClass = draggable ? (dragging ? 'cursor-grabbing' : 'cursor-grab') : '';

  return (
    <div className="flex-1 m-4 mb-2 rounded-xl bg-viewer-bg flex flex-col items-center justify-center relative overflow-hidden">
      <div className="absolute inset-0 viewer-dots pointer-events-none"/>

      {filename && (
        <div className="absolute top-3.5 left-3.5 z-[3] bg-black/35 backdrop-blur px-2.5 py-1 rounded text-white/80 text-xs2 font-mono tracking-tight border border-white/10">
          {filename}
        </div>
      )}

      {imgError && (
        <div className="absolute inset-0 z-[4] flex items-center justify-center pointer-events-none">
          <div className="bg-surface/95 border border-border-soft rounded-lg px-4 py-3 flex items-center gap-2.5 text-sm text-text-strong shadow-lg max-w-[80%]">
            <AlertCircle size={16} strokeWidth={2} className="text-danger flex-shrink-0"/>
            <span>{t('print.preview_render_failed')}</span>
          </div>
        </div>
      )}

      {oversize && (
        <div className="absolute top-3.5 right-3.5 z-[3] bg-danger text-white px-3 py-2 rounded flex items-start gap-2 text-xs font-medium shadow-lg max-w-[340px]">
          <AlertCircle size={13} strokeWidth={2} className="flex-shrink-0 mt-0.5"/>
          <div className="flex-1 min-w-0">
            {blocking_issues.length > 0 ? (
              // Precise backend messages (B13.1: 3 distinct cases per axis).
              // We show ALL the messages — typically 1-2, sometimes 4
              // (image too wide + too tall for example).
              blocking_issues.map((msg, i) => (
                <div key={i} className={i > 0 ? 'mt-1 leading-snug' : 'leading-snug'}>
                  {msg}
                </div>
              ))
            ) : (
              // Fallback if the backend did not return any messages
              // (theoretically never, but defensive for mocks
              // or an old backend).
              <div>{t('print.preview_oversize_fallback')}</div>
            )}
            {overflowParts.length > 0 && (
              <div className="font-mono text-[10.5px] opacity-75 mt-1">
                {overflowParts.join(' · ')}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Paper frame */}
      <div className="relative z-[2]" style={{ width: pw, height: ph }}>
        <div className="absolute inset-0 bg-paper shadow-paper-light"/>

        {/* Printable zone — informative, discreet dashes */}
        {hasPrintable && (
          <div
            className="absolute pointer-events-none"
            style={{
              left: prx, top: pry, width: prw, height: prh,
              border: '1px dashed rgba(120,120,120,0.40)',
            }}/>
        )}

        {/* Sheet centering crosshair — visible only during the drag */}
        {dragging && (
          <>
            <div className="absolute pointer-events-none bg-white/30"
                 style={{ left: pw / 2 - 6, top: ph / 2 - 0.5, width: 12, height: 1 }}/>
            <div className="absolute pointer-events-none bg-white/30"
                 style={{ left: pw / 2 - 0.5, top: ph / 2 - 6, width: 1, height: 12 }}/>
          </>
        )}

        {/* Image — real backend preview (PNG ≤ 1024 px max side), with
            grayscale + red directional overlays on the portions that
            overflow the printable zone if oversize. */}
        <div
          className={`absolute overflow-hidden ${cursorClass}`}
          onMouseDown={handleMouseDown}
          onDoubleClick={handleDoubleClick}
          style={{
            left: display_ix, top: display_iy, width: iw, height: ih,
            outline: oversize ? '1.5px solid var(--danger)' : '1px solid rgba(0,0,0,0.12)',
            // Anti-flash during the PNG load (~200 ms locally)
            background: 'rgba(50,50,50,0.15)',
            userSelect: 'none',
          }}>
          {file_id && !imgError && (
            <img
              src={getFilePreviewUrl(file_id)}
              alt={filename || t('print.preview_alt_default')}
              draggable={false}
              onError={() => setImgError(true)}
              className="block pointer-events-none select-none"
              style={{
                position: 'absolute',
                width: imgNativeW, height: imgNativeH,
                // maxWidth:'none' neutralizes Tailwind's preflight `img{max-width:100%}`
                // on THIS image only. The rotation trick sizes the <img> to the NATIVE
                // footprint (imgNativeW = ih in the rotated case), i.e. WIDER than its
                // container when the effective footprint is portrait; the preflight cap
                // would shrink it so rotate() no longer fills the container (grey gap).
                maxWidth: 'none',
                left: (iw - imgNativeW) / 2, top: (ih - imgNativeH) / 2,
                transform: `rotate(${orientation}deg)`,
                transformOrigin: 'center center',
                objectFit: 'cover',
                filter: oversize ? 'grayscale(1)' : 'none',
              }}/>
          )}

          {/* Semi-transparent red overlays on the portions that overflow
              the printable zone. Width/height = overflow_*_mm × ppm. */}
          {oversize && overflow_mm?.left > 0 && (
            <div className="absolute left-0 top-0 h-full pointer-events-none"
                 style={{ width: overflow_mm.left * ppm, background: 'rgba(200,54,43,0.5)' }}/>
          )}
          {oversize && overflow_mm?.top > 0 && (
            <div className="absolute left-0 top-0 w-full pointer-events-none"
                 style={{ height: overflow_mm.top * ppm, background: 'rgba(200,54,43,0.5)' }}/>
          )}
          {oversize && overflow_mm?.right > 0 && (
            <div className="absolute right-0 top-0 h-full pointer-events-none"
                 style={{ width: overflow_mm.right * ppm, background: 'rgba(200,54,43,0.5)' }}/>
          )}
          {oversize && overflow_mm?.bottom > 0 && (
            <div className="absolute left-0 bottom-0 w-full pointer-events-none"
                 style={{ height: overflow_mm.bottom * ppm, background: 'rgba(200,54,43,0.5)' }}/>
          )}
        </div>

        {/* Paper outline on top so it's always visible */}
        <div className="absolute inset-0 pointer-events-none border border-white/55"/>

        {/* Crop marks at the sheet corners (not the printable zone) */}
        {[[0,0],[1,0],[0,1],[1,1]].map(([x,y]) => (
          <div key={`${x}-${y}`}
               className="absolute w-3.5 h-3.5 pointer-events-none"
               style={{
                 left: x ? pw - 1 : 0,
                 top:  y ? ph - 1 : 0,
                 borderLeft:   !x ? '1px solid rgba(255,255,255,0.5)' : 'none',
                 borderRight:   x ? '1px solid rgba(255,255,255,0.5)' : 'none',
                 borderTop:    !y ? '1px solid rgba(255,255,255,0.5)' : 'none',
                 borderBottom:  y ? '1px solid rgba(255,255,255,0.5)' : 'none',
                 transform: `translate(${x ? -13 : -1}px, ${y ? -13 : -1}px)`,
               }}/>
        ))}
      </div>

      {/* Drag tooltip — position fixed (viewport coords), follows the cursor */}
      {dragging && (
        <div
          className="fixed z-50 bg-surface border border-border-soft rounded-md shadow-lg
                     px-2.5 py-1.5 text-xs2 font-mono tabular-nums text-text-strong
                     pointer-events-none whitespace-nowrap"
          style={{ left: cursorPos.x + 14, top: cursorPos.y + 14 }}>
          X {finalPosX.toFixed(1)} mm · Y {finalPosY.toFixed(1)} mm
        </div>
      )}

      {/* Info strip */}
      <div className="my-4 flex items-center gap-3.5 z-[2] font-mono tabular-nums">
        <span className="text-white/95 text-[13px] tracking-wide">{Math.round(imgW_mm)} × {Math.round(imgH_mm)} mm</span>
        <span className="text-white/30">·</span>
        <span className="text-white/60 text-[13px]">{dpi} {t('print.preview_dpi_unit')}</span>
        <span className="text-white/30">·</span>
        <span className={`text-[13px] ${oversize ? 'text-[#ff8a82]' : 'text-white/60'}`}>
          {oversize
            ? t('print.preview_info_overflow', { detail: overflowParts.join(' · ') || '—' })
            : centered && !dragging && !localPos
              ? t('print.preview_info_centered')
              : t('print.preview_info_margins', { x: Math.round(display_x_mm), y: Math.round(display_y_mm) })}
        </span>
      </div>
    </div>
  );
}

