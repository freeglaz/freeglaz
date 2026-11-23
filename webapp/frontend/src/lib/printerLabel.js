/**
 * Printer name shown in the top bar and the status bar: the model the printer
 * reports (status.model, its identification ModelName), with the support badge
 * of its family (status.family from printer_model.classify on the backend).
 * Falls back to the historical "HP DesignJet Z9" while the model is unknown
 * (not configured, unreachable: the backend reports "?").
 */
export const DEFAULT_PRINTER_NAME = 'HP DesignJet Z9';

/** @returns {{ name: string, badge: 'partial' | 'untested' | null }} */
export function printerLabel(model, family) {
  const m = typeof model === 'string' ? model.trim() : '';
  const name = m && m !== '?' ? m : DEFAULT_PRINTER_NAME;
  const badge = family === 'z9pro' ? 'partial' : family === 'designjet' ? 'untested' : null;
  return { name, badge };
}
