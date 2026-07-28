import { useTranslation } from 'react-i18next';

const ORIGIN_STYLE = {
  hp_ingenium: { dot: 'var(--hp-origin)', label_key: 'inspector.origin.hp_ingenium' },
  xrite:       { dot: '#E07700', label_key: 'inspector.origin.xrite' },
  argyll:      { dot: '#7B4FB6', label_key: 'inspector.origin.argyll' },
  lcms:        { dot: '#3A8540', label_key: 'inspector.origin.lcms' },
  adobe:       { dot: '#C53030', label_key: 'inspector.origin.adobe' },
  apple:       { dot: '#666666', label_key: 'inspector.origin.apple' },
  unknown:     { dot: '#666666', label_key: 'inspector.origin.unknown' },
};


/**
 * Maps the ``profile_type_guess`` returned by the backend inspector to
 * a compact origin key used for the badge.
 *
 * possible profile_type_guess:
 *   - "factory" / "user-default" / "user-custom"   → hp_ingenium
 *   - "non-hp (X-Rite/i1Profiler)"                  → xrite
 *   - "non-hp (basICColor)" / "non-hp (Argyll CMS)" → argyll
 *   - "non-hp (lcms (Little CMS))"                  → lcms
 *   - "non-hp (Adobe)" / "non-hp (Apple ColorSync)" → adobe / apple
 *   - others                                         → unknown
 */
export function originKeyFromInspection(inspection) {
  if (!inspection) return 'unknown';
  if (inspection.is_hp_ingenium) return 'hp_ingenium';
  const guess = (inspection.profile_type_guess || '').toLowerCase();
  if (guess.includes('x-rite') || guess.includes('xrite')) return 'xrite';
  if (guess.includes('argyll')) return 'argyll';
  if (guess.includes('lcms') || guess.includes('little cms')) return 'lcms';
  if (guess.includes('adobe')) return 'adobe';
  if (guess.includes('apple')) return 'apple';
  if (guess.includes('basiccolor')) return 'argyll';
  return 'unknown';
}


export default function OriginBadge({ originKey, size = 'md' }) {
  const { t } = useTranslation();
  const style = ORIGIN_STYLE[originKey] || ORIGIN_STYLE.unknown;
  const pad = size === 'sm' ? 'px-1.5 py-px text-[10px]' : 'px-2 py-0.5 text-[11px]';
  return (
    <span
      className={`inline-flex items-center gap-1.5 ${pad} rounded font-medium bg-sunken text-text-strong border border-border-soft`}>
      <span
        className="w-1.5 h-1.5 rounded-full shrink-0"
        style={{ backgroundColor: style.dot }}
        aria-hidden="true"/>
      {t(style.label_key)}
    </span>
  );
}
