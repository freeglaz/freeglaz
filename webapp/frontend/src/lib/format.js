// Formatting helpers — mm, dpi, margins. Always return strings ready
// to paste into the UI. The precision is fixed on purpose (integers for
// mm because we're talking about physical prints, not microns).

// Non-component module → resolve labels through the i18next singleton.
import i18n from '../i18n';

export const fmtMm   = (n) => `${Math.round(n)} mm`;
export const fmtDpi  = (n) => `${Math.round(n)} dpi`;
export const fmtDims = (w, h) => `${Math.round(w)} × ${Math.round(h)} mm`;

// 1-decimal variants — used for paper dimensions measured by
// the Z9 (firmware returns sub-millimeter floats: 210.1003418 mm).
// Math.round to the integer in this case would be imprecise ("210" for 210.1 mm
// vs "209" for 209.4 mm → not great on the fine-art print UI).
export const fmtMm1   = (n) => `${(n).toFixed(1)} mm`;
export const fmtDims1 = (w, h) => `${(w).toFixed(1)} × ${(h).toFixed(1)} mm`;

export function fmtMargins({ paperW, paperH, imgW, imgH }) {
  const mx = Math.round((paperW - imgW) / 2);
  const my = Math.round((paperH - imgH) / 2);
  return i18n.t('print.margins_label', { x: mx, y: my });
}

export function fmtOverflow({ paperW, paperH, imgW, imgH }) {
  const ox = Math.round(imgW - paperW);
  const oy = Math.round(imgH - paperH);
  return i18n.t('print.overflow_label', { x: ox, y: oy });
}

export const fmtPct = (n) => `${Math.round(n)} %`;
