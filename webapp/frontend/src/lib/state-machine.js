// Single source of truth for the UI state. Everything derives from here. Do not
// duplicate it in separate useState hooks.

export const UIState = {
  A_EMPTY:     'A_empty',
  B_READY:     'B_ready',
  C_OVERSIZED: 'C_oversized',
  D_NOPAPER:   'D_nopaper',
  E_PRINTING:  'E_printing',
  F_MISMATCH:  'F_mismatch',
};

// Webapp worker states that terminate a job (no longer active).
// Reversal B16 (23/05/2026): E_PRINTING is now worker-driven,
// not firmware-driven. Increment 11.2 (f7b54f2) had tied E_PRINTING to
// `status.z9_activity` to track the real printing duration on the
// machine — but that locked the main UI for several
// minutes of Drying, preventing a new file from being dropped. HP
// Click model adopted: active worker → E_PRINTING, finished worker → reset UI
// to A_EMPTY after a brief feedback. The physical tracking of the Z9
// (Processing/Drying/NoActivity) stays informative in the sidebar
// `z9_activity` and the StatusBar, independent of the main state.
const WORKER_TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled']);

/**
 * @param {{
 *   status: import('../api/types').Status | null,
 *   file: import('../api/types').FileWithPreview | null,
 *   workerActive?: boolean,
 * }} input
 * @returns {string}
 */
export function deriveUIState({ status, file, workerActive = false }) {
  if (!status || status.paper === null) return UIState.D_NOPAPER;
  if (workerActive)                      return UIState.E_PRINTING;
  if (!file)                             return UIState.A_EMPTY;
  if (file.preview && !file.preview.fits) return UIState.C_OVERSIZED;
  if (file.info?.icc_status === 'mismatch') return UIState.F_MISMATCH;
  return UIState.B_READY;
}

/**
 * Helper to compute workerActive from (jobId, jobState).
 * Centralizes the list of terminal states to avoid divergences.
 */
export function isWorkerActive(jobId, jobState) {
  if (!jobId) return false;
  if (!jobState) return true;  // jobId set but SSE not connected yet
  return !WORKER_TERMINAL_STATES.has(jobState);
}

export const isPrintable = (state) =>
  state === UIState.B_READY || state === UIState.F_MISMATCH;
