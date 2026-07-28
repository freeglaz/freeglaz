import { useTranslation } from 'react-i18next';
import IccSlot from './IccSlot.jsx';

/**
 * Zone 2 — ICC profiles (spec §5 Zone 2). Slots stacked vertically
 * — each slot takes the full width to fit the
 * 4 actions on a single line.
 */
const _GE_OF_KIND = { ge_off: 'off', ge_on: 'on', single: 'single' };

export default function IccZone({ paper, onChanged, onNotice, onProfileSlot, autoInstallSlot = null, offline = false }) {
  const { t } = useTranslation();
  const icc = paper.icc || [];
  const empty = icc.length === 0;

  return (
    <section className="px-5 py-4 border-b border-border-soft">
      <h3 className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted mb-3">
        {t('papers.detail.section_icc')}
      </h3>

      {empty ? (
        <p className="text-xs2 text-text-faint italic">
          {t('papers.icc.no_profiles')}
        </p>
      ) : (
        <div className="flex flex-col gap-2.5">
          {icc.map((slot, i) => (
            <IccSlot
              key={`${slot.kind}-${i}`}
              slot={slot}
              mediaid={paper.mediaid}
              paperFactory={!!paper.factory}
              paperName={paper.name}
              offline={offline}
              autoOpenInstall={!!autoInstallSlot && _GE_OF_KIND[slot.kind] === autoInstallSlot}
              onChanged={onChanged}
              onNotice={onNotice}
              onProfile={onProfileSlot ? () => {
                const ge = slot.kind === 'ge_on' ? true : slot.kind === 'ge_off' ? false : undefined;
                onProfileSlot(paper, ge);
              } : null}/>
          ))}
        </div>
      )}

    </section>
  );
}
