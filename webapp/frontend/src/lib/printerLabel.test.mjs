/**
 * Tests of lib/printerLabel.js (printer name in the bars).
 * Run: node webapp/frontend/src/lib/printerLabel.test.mjs
 */
import { DEFAULT_PRINTER_NAME, printerLabel } from './printerLabel.js';

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }

const pro = printerLabel('HP DesignJet Z9 Pro 64in PS', 'z9pro');
assert(pro.name === 'HP DesignJet Z9 Pro 64in PS' && pro.badge === 'partial', 'Z9 Pro: its real model, partial support');
const z9 = printerLabel('HP DesignJet Z9+ 24in', 'z9');
assert(z9.name === 'HP DesignJet Z9+ 24in' && z9.badge === null, 'Z9: its model, no badge');
assert(printerLabel('HP DesignJet T1700', 'designjet').badge === 'untested', 'other DesignJet: untested');
assert(printerLabel('?', null).name === DEFAULT_PRINTER_NAME, 'unknown model ("?"): historical name');
assert(printerLabel(undefined, undefined).name === DEFAULT_PRINTER_NAME, 'no status yet: historical name');
assert(printerLabel('  HP DesignJet Z9 Pro 64in PS ', 'z9pro').name === 'HP DesignJet Z9 Pro 64in PS', 'trimmed');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
