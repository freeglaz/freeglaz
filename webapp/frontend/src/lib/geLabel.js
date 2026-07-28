// SINGLE presentation of the Gloss Enhancer (single point — never « FULLPAGE » on screen).
//
// The INTERNAL values 'FULLPAGE'/'OFF' (firmware, manifests, slots, SOAP, guards) NEVER
// change — we translate ONLY the presentation. The GE hack does not apply a
// « full page » varnish (selective application) → the firmware term « FULLPAGE » is misleading:
// we display « GE ON ». GE absent/null/false → « GE OFF » (consistent with _norm_ge backend).
export function geLabel(v) {
  return (v === 'FULLPAGE' || v === 'ON' || v === true) ? 'GE ON' : 'GE OFF';
}
