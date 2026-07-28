import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Info, Table2, ShieldCheck } from 'lucide-react';
import DeltaEHistogram from './DeltaEHistogram';
import AllPatchesModal from './AllPatchesModal';

/**
 * Profcheck report body SHARED between:
 *   - (a) self-consistency (scope='self') — optimistic, creation patches, no reprint.
 *   - (b) independent validation (scope='independent') — true predictive accuracy (Mechanism 1).
 *
 * The verdict (IN-HOUSE grid) remains the main judgment, at the top. The ISO 12647
 * benchmarks are a scale MENTION (on the histogram), NOT a second verdict. The honesty
 * caveat is parameterized by `scope` (the UI must always display it).
 */
const _QUALITY_COLOR = {
  'Excellent': 'text-icc-ok-deep',
  'Good': 'text-icc-ok-deep',
  'Fair': 'text-icc-warn-deep',
  'Poor': 'text-danger',
};
const _N_WORST = 8;
const _rgbCss = (rgb) => `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;

export default function ProfcheckReport({ report, scope = 'self', profileName }) {
  const { t } = useTranslation();
  const [allOpen, setAllOpen] = useState(false);

  const pc = report?.profcheck;
  const wp = report?.white_point_lab;
  const indep = report?.independence;
  const patches = pc?.patches || [];
  const worst = patches.slice(0, _N_WORST);
  const isIndependent = scope === 'independent';

  return (
    <div className="space-y-4 text-sm text-text-strong">
      {/* Honesty warning — ALWAYS visible, at the top, depending on the scope */}
      <div className="flex items-start gap-2 text-xs2 text-icc-warn-deep bg-icc-warn/10 border border-icc-warn/25 rounded-md px-3 py-2.5">
        <AlertTriangle size={14} className="mt-0.5 flex-shrink-0"/>
        <span>{t(isIndependent ? 'profils.validate_caveat' : 'profils.check_caveat')}</span>
      </div>

      {report?.status === 'no_measurements' && (
        <div className="flex items-start gap-2 text-xs2 text-text-strong bg-sunken/60 rounded-md px-3 py-2.5">
          <Info size={14} className="mt-0.5 text-accent"/><span>{t('profils.check_no_measurements')}</span>
        </div>
      )}

      {pc?.status === 'ok' && (
        <>
          <div className="flex items-baseline gap-2">
            <span className="text-text-muted text-xs2">{t('profils.check_verdict')}</span>
            <span className={`text-base font-semibold ${_QUALITY_COLOR[pc.quality] || 'text-text-strong'}`}>
              {pc.quality}
            </span>
          </div>
          <dl className="text-xs2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            <dt className="text-text-muted">{t('profils.check_avg')}</dt><dd className="font-mono">ΔE₀₀ {pc.avg_de2000}</dd>
            <dt className="text-text-muted">{t('profils.check_max')}</dt><dd className="font-mono">ΔE₀₀ {pc.peak_de2000}</dd>
            <dt className="text-text-muted">{t('profils.check_rms')}</dt><dd className="font-mono">ΔE₀₀ {pc.rms_de2000}</dd>
            {wp && (
              <>
                <dt className="text-text-muted">{t('profils.check_white')}</dt>
                <dd className="font-mono">
                  L*{wp.L} a*{wp.a} b*{wp.b}
                  {wp.suspect && <span className="text-icc-warn-deep"> · {t('profils.check_white_suspect')}</span>}
                </dd>
              </>
            )}
          </dl>

          {/* Independence (b only) — verified, not assumed */}
          {isIndependent && indep && (
            <div className="flex items-start gap-2 text-xs2 rounded-md px-3 py-2.5 border bg-sunken/50 border-border-soft">
              {indep.compromised
                ? <AlertTriangle size={14} className="mt-0.5 text-icc-warn-deep flex-shrink-0"/>
                : <ShieldCheck size={14} className="mt-0.5 text-icc-ok-deep flex-shrink-0"/>}
              <span>
                {indep.creation_known === false
                  ? t('profils.validate_indep_unknown')
                  : indep.compromised
                    ? t('profils.validate_indep_bad', { pct: indep.pct_overlap })
                    : t('profils.validate_indep_ok', { pct: indep.pct_overlap ?? 0 })}
              </span>
            </div>
          )}

          {patches.length > 0 && (
            <div className="border-t border-border-soft pt-3">
              <DeltaEHistogram patches={patches}/>
            </div>
          )}

          {worst.length > 0 && (
            <div className="border-t border-border-soft pt-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-text-muted text-xs2">{t('profils.check_worst_title', { n: worst.length })}</span>
                <button type="button" onClick={() => setAllOpen(true)}
                        className="text-xs2 font-semibold inline-flex items-center gap-1.5 text-accent hover:underline">
                  <Table2 size={13}/> {t('profils.check_see_all', { n: patches.length })}
                </button>
              </div>
              <table className="w-full text-tiny font-mono">
                <thead>
                  <tr className="text-text-faint text-left">
                    <th className="font-medium pb-1">ΔE₀₀</th>
                    <th className="font-medium pb-1"/>
                    <th className="font-medium pb-1">RGB</th>
                    <th className="font-medium pb-1">{t('profils.check_all_lab_target')}</th>
                  </tr>
                </thead>
                <tbody>
                  {worst.map((p) => (
                    <tr key={p.id} className="text-text-muted">
                      <td className="text-text-strong pr-2">{p.de2000.toFixed(3)}</td>
                      <td className="pr-2">
                        <span className="inline-block w-3.5 h-3.5 rounded-sm border border-border-soft align-middle"
                              style={{ background: _rgbCss(p.rgb) }}/>
                      </td>
                      <td className="pr-3">{p.rgb.join(' ')}</td>
                      <td>{p.lab_target.map((v) => v.toFixed(1)).join(' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* ISO scale benchmark — CONTEXTUAL, not a second verdict */}
          <p className="text-tiny text-text-faint leading-snug">{t('profils.check_iso_ref')}</p>
        </>
      )}

      {pc && pc.status !== 'ok' && report?.status !== 'no_measurements' && (
        <div className="flex items-start gap-2 text-xs2 text-text-muted bg-sunken/60 rounded-md px-3 py-2.5">
          <Info size={14} className="mt-0.5"/><span>{t('profils.check_unavailable', { reason: pc.reason || pc.status })}</span>
        </div>
      )}

      <AllPatchesModal open={allOpen} patches={patches} title={profileName}
                       onClose={() => setAllOpen(false)}/>
    </div>
  );
}
