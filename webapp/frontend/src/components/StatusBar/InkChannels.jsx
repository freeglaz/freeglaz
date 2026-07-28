// Colors of the 10 Z9+ ink channels. Ad-hoc short codes that stick to the
// firmware's real names (yellow/magenta/cyan/photo-black/matte-black/
// chromatic-{red,blue,green}/gray/post-treatment) rather than the legacy
// HP DesignJet mapping (Y/M/C/K/LC/LM/Cl/GE) which does not match
// the Z9+.
const INK_COLORS = {
  Y:  '#F2C400',  // yellow
  M:  '#C92778',  // magenta
  C:  '#1A8FCB',  // cyan
  PK: '#1A1A1A',  // photo-black
  MK: '#3A3A3A',  // matte-black
  CR: '#D03A2A',  // chromatic-red
  CB: '#1A4FA8',  // chromatic-blue
  CG: '#2C8A4F',  // chromatic-green
  GY: '#888583',  // gray
  GE: '#D4D0C7',  // post-treatment (gloss enhancer)
};

// Fixed display order (visual consistency, independent of the order in
// which the backend returns the inks).
const ORDER = ['Y', 'M', 'C', 'PK', 'MK', 'CR', 'CB', 'CG', 'GY', 'GE'];

export default function InkChannels({ inks }) {
  // `inks` arrives as [{ id, level, state? }] — we index them by id.
  const byId = Object.fromEntries((inks || []).map((i) => [i.id, i.level]));
  return (
    <div className="flex gap-2 items-center">
      {ORDER.map((id) => {
        const level = byId[id] ?? 0;
        return (
          <div key={id} className="flex items-center gap-1.5">
            <span className="text-[9.5px] font-mono text-text-faint tracking-wider w-4 text-right">{id}</span>
            <div className="relative w-[36px] h-1.5 bg-sunken rounded-full overflow-hidden">
              <div
                className="absolute inset-y-0 left-0 rounded-full"
                style={{ width: `${level * 100}%`, background: INK_COLORS[id] || '#888' }}/>
            </div>
          </div>
        );
      })}
    </div>
  );
}
