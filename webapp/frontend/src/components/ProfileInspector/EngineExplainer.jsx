import { useTranslation } from 'react-i18next';

/**
 * Explanatory box for the boundary rendering engine.
 *
 * Collapsible, short, factual. Shared between 2D slices (slice2d) and
 * Mapping. The content (3 lines) explains:
 *  - boundaries = direct A2B evaluation via lcms2 (profile's view)
 *  - no BPC, no source gamut mapping here
 *  - same engine (lcms2) as at print time → representative boundaries
 */
export default function EngineExplainer({ i18nPrefix }) {
  const { t } = useTranslation();
  return (
    <details className="group rounded-md border border-border-soft bg-sunken/30
                        text-xs2 leading-relaxed text-text-muted">
      <summary className="cursor-pointer select-none px-3 py-1.5
                          flex items-center gap-2 list-none">
        <span className="text-text-faint text-tiny uppercase tracking-wider">
          {t(`${i18nPrefix}.engine_summary`)}
        </span>
        <span className="ml-auto text-text-faint text-tiny group-open:hidden">
          ▸
        </span>
        <span className="ml-auto text-text-faint text-tiny hidden group-open:inline">
          ▾
        </span>
      </summary>
      <div className="px-3 pb-2.5 pt-1 space-y-1.5">
        <p>{t(`${i18nPrefix}.engine_a2b`)}</p>
        <p>{t(`${i18nPrefix}.engine_no_bpc`)}</p>
        <p>{t(`${i18nPrefix}.engine_consistency`)}</p>
      </div>
    </details>
  );
}
