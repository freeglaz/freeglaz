// Natural ("human") ordering for patch identifiers: "2" < "10", "A2" < "A10".
// SAMPLE_ID tokens are strings in the ti3 (numeric or not), so a plain `>`
// comparison sorts them lexicographically ("10" before "2").
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

/** Comparator for Array.prototype.sort — natural order, null/undefined last. */
export function compareNatural(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return collator.compare(String(a), String(b));
}
