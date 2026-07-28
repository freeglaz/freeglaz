/**
 * UI ↔ backend mappings for the print parameters.
 *
 * Convention:
 *   - Internal frontend state: short semantic key (e.g. "image", "off")
 *   - Backend payload         : enum value recognized by the firmware/PJL
 *   - UI label                : text displayed to the user (FR)
 *
 * The frontend NO LONGER holds the backend enums hardcoded. Every
 * conversion goes through the helpers below. Backend UNCHANGED —
 * still accepts/sends the historical enums (FULLPAGE, OFF, etc.).
 */

import i18n from '../i18n';

// ─── Gloss Enhancer ──────────────────────────────────────────────────────
// The term "FULLPAGE" comes from the HP/PJL firmware and is counter-intuitive
// (suggests "the whole sheet" whereas the GE is applied only on
// the image area). We expose "On the image" / "Off" to the user.
// `label` is a getter so it resolves through i18n.t at read time (the
// active language may change after this module is first evaluated).

export const GLOSS_ENHANCER_UI = {
  image: { backend: 'FULLPAGE', get label() { return i18n.t('print.gloss_enhancer_option_image'); } },
  off:   { backend: 'OFF',      get label() { return i18n.t('print.gloss_enhancer_option_off');   } },
};

export function uiGEToBackend(uiKey) {
  return GLOSS_ENHANCER_UI[uiKey]?.backend ?? 'OFF';
}

export function backendGEToUI(backendValue) {
  return backendValue === 'FULLPAGE' ? 'image' : 'off';
}

// Resolved via i18n.t at call time (function, not a const, so the string
// follows the active language rather than being frozen at import).
export function glossEnhancerHelp() {
  return i18n.t('print.gloss_enhancer_help_default');
}
