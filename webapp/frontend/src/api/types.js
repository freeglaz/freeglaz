// Shared JSDoc typedefs. No TypeScript in this scaffold — we move faster
// with plain JSX. If the project switches to TS, convert these typedefs
// into interfaces.

/**
 * @typedef {Object} Paper
 * @property {'sheet'|'roll'} kind
 * @property {string} name              e.g.: "Canson Baryta Photographique II"
 * @property {number} width_mm
 * @property {number} [height_mm]       absent if roll
 * @property {string[]} capabilities    e.g.: ['gloss_enhancer','max_detail']
 * @property {string} icc_profile       e.g.: 'HP_Z9_Canson_Baryta_GEON'
 */

/**
 * @typedef {Object} Ink
 * @property {string} id      'Y' | 'M' | 'C' | 'K' | 'LC' | 'LM' | 'Cl' | 'GE'
 * @property {number} level   0..1
 */

/**
 * @typedef {Object} Job
 * @property {string} id
 * @property {number} progress     0..1
 * @property {'queued'|'sending'|'printing'|'completed'|'failed'|'cancelled'} state
 * @property {string} filename
 */

/**
 * @typedef {Object} Z9Activity
 * @property {string} name      e.g.: "Processing", "Drying", "NoActivity"
 * @property {number|null} progress_pct  % if exposed by the firmware
 */

/**
 * @typedef {Object} Status
 * @property {'ready'|'busy'|'error'} z9_state
 * @property {Paper|null} paper
 * @property {Ink[]} inks
 * @property {Job|null} current_job
 * @property {Z9Activity|null} z9_activity  PHYSICAL activity of the Z9.
 *   Intentionally desynchronized from current_job: the webapp worker can
 *   be DONE well before the Z9 has finished printing + drying.
 *   E_PRINTING in the UI is derived from here, not from the webapp job.
 */

/**
 * @typedef {Object} FileInfo
 * @property {string} id
 * @property {string} filename
 * @property {number} width_mm
 * @property {number} height_mm
 * @property {number} dpi
 * @property {string|null} icc_profile          e.g.: 'sRGB IEC61966-2.1'
 * @property {string|null} [icc_md5]            MD5 hex of the embedded ICC bytes
 * @property {'none'|'match'|'mismatch'|'unknown'} icc_status
 *   none     — file without ICC (deterministic info, gray)
 *   match    — profiles confirmed identical
 *   mismatch — profiles confirmed different (red, distorted rendering)
 *   unknown  — comparison impossible on the firmware side (amber, alert)
 */

/**
 * @typedef {Object} PrintPreview
 * @property {boolean} fits
 * @property {{ x: number, y: number }} [overflow_mm]
 * @property {{ x: number, y: number }} [margin_mm]
 */

/**
 * @typedef {Object} FileWithPreview
 * @property {FileInfo} info
 * @property {PrintPreview} preview
 */

export {};
