/**
 * Regression guard for a TDZ bug that shipped twice: a `setTimeout` handle
 * named `t` inside a block that also calls the i18n `t(...)`. Because `const t`
 * is block-scoped with a Temporal Dead Zone, the earlier `t(...)` call refers to
 * the not-yet-initialised timeout handle → `ReferenceError` at runtime.
 *
 * It bit `JobQueuePanel.jsx` (crash when a previewed job left the queue) and
 * `App.jsx` (the print "completed" branch threw, killing the success toast +
 * reset). There is no React test harness here, so this guards the *shape*:
 *   1. the TDZ semantics are real (buggy shape throws, renamed shape is fine);
 *   2. neither file reintroduces `const t = setTimeout(...)`.
 *
 * Run: node webapp/frontend/src/lib/tdz_timeout_shadow.test.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.error(`  ✗ ${msg}`); }
}

const here = dirname(fileURLToPath(import.meta.url));
const srcRoot = join(here, '..');  // webapp/frontend/src

// ─── Test 1: the TDZ semantics this bug relies on ───────────────────
console.log('Test 1 — TDZ semantics (buggy shape throws, fixed shape does not)');
{
  const t = (k) => `i18n:${k}`;               // the outer i18n function
  const buggy = () => {
    const notice = t('some.key');             // uses block-scoped `t` below → TDZ
    const t = setTimeout(() => {}, 0);        // eslint-disable-line no-unused-vars
    clearTimeout(t);
    return notice;
  };
  let threw = false;
  try { buggy(); } catch (e) { threw = e instanceof ReferenceError; }
  assert(threw, 'the `const t = setTimeout` shape throws a ReferenceError');

  const fixed = () => {
    const notice = t('some.key');             // outer i18n `t`, no shadow
    const timer = setTimeout(() => {}, 0);
    clearTimeout(timer);
    return notice;
  };
  let ok = false;
  try { ok = fixed() === 'i18n:some.key'; } catch { ok = false; }
  assert(ok, 'renaming the handle (`timer`) removes the TDZ');
}

// ─── Test 2: the two files do not reintroduce the anti-pattern ──────
console.log('\nTest 2 — no `const t = setTimeout(...)` in the guarded files');
for (const rel of ['App.jsx', 'components/JobQueue/JobQueuePanel.jsx']) {
  const text = readFileSync(join(srcRoot, rel), 'utf8');
  // whitespace-tolerant: `const t = setTimeout` / `const t=setTimeout`
  const bad = /\bconst\s+t\s*=\s*setTimeout\b/.test(text);
  assert(!bad, `${rel} has no timeout handle shadowing the i18n \`t\``);
}

// ─── Summary ────────────────────────────────────────────────────────
console.log(`\n──── ${pass} pass / ${fail} fail ────`);
if (fail > 0) process.exit(1);
