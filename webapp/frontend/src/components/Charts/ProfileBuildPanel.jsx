import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { HelpCircle, Save, RefreshCw, Loader2 } from 'lucide-react';
import * as api from '../../api/client.js';
import Segmented from '../ui/Segmented.jsx';

// Max profile name length = ICC V2 desc limit (consistent with backend
// cache.REPO_Z9_NAME_MAXLEN; Argyll = V2 only). Blocks the input + explanatory message.
const NAME_MAXLEN = 63;

// Profile build CONTROL panel — SHARED wizard + Measurements (one single path, zero
// divergence: the "no parallel path" lesson). Presentational: base selector
// (Average(N) | Last) + EDITABLE colprof flags + presets + help + "will run: colprof ..." line
// (mandatory VISIBILITY) + OPTIONAL [Build] button (Measurements uses it; the wizard keeps
// its footer → no regression). The background job EXECUTES; here we choose/show WHAT will be run.
function _Select({ value, onChange, options }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
            className="bg-sunken rounded px-2 py-1 text-xs2 text-text-strong border border-border-soft">
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export default function ProfileBuildPanel({
  nIncluded = 0,
  base, setBase,
  colprofFlags, setColprofFlags, colprofPresets = [],
  colprofPresetKey, setColprofPresetKey, saveColprofPreset,
  loadColprofHelp, showColprofHelp, colprofHelp,
  onBuild, busy = false, buildPhase = null, buildLabel,     // optional button (otherwise parent footer)
  chartId = null, onSelectionChange,                        // additional sources
  nameNotice = null,                                        // ASCII/length only (BELOW the field; collision → parent pop-up)
}) {
  const { t } = useTranslation();

  // ── ADDITIONAL sources (multi-source build) ────────────────────────────────
  // Enumerated server-side (same paper+GE, authoritative); selection bubbled up to the
  // parent which adds it to the build body. The current chart is included
  // IMPLICITLY (not in this list).
  const [sources, setSources] = useState(null);
  const [selCharts, setSelCharts] = useState(() => new Set());
  const [selProfiles, setSelProfiles] = useState(() => new Set());
  // Profile name: pre-filled with the auto suggestion (= `HPZ9_…` naming), editable.
  const [name, setName] = useState('');
  const suggested = sources?.suggested_name || '';
  const nameAscii = !/[^\x00-\x7F]/.test(name);

  useEffect(() => {
    setSources(null); setSelCharts(new Set()); setSelProfiles(new Set()); setName('');
    if (!chartId) return undefined;
    let cancelled = false;
    api.getBuildSources(chartId)
      .then((d) => { if (!cancelled) { setSources(d); setName(d.suggested_name || ''); } })
      .catch(() => { if (!cancelled) setSources({ extra_charts: [], profiles: [] }); });
    return () => { cancelled = true; };
  }, [chartId]);

  useEffect(() => {
    onSelectionChange?.({
      extra_chart_ids: [...selCharts],
      source_profiles: [...selProfiles].map((p) => ({ path: p })),
      // null if empty / unchanged (= auto default) → no regression; otherwise the custom name.
      name: (name.trim() && name.trim() !== suggested) ? name.trim() : null,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selCharts, selProfiles, name, suggested]);

  const toggleChart = (cid) => setSelCharts((s) => {
    const n = new Set(s); n.has(cid) ? n.delete(cid) : n.add(cid); return n;
  });
  const toggleProfile = (p) => setSelProfiles((s) => {
    const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n;
  });
  const hasSources = sources && ((sources.extra_charts || []).length > 0
                                 || (sources.profiles || []).length > 0);

  return (
    <div className="space-y-3">
      {/* Profile name — pre-filled with the auto naming, editable (ASCII). Unchanged =
          auto default (no regression). Collision handled at submission (refusal + suggestion -2). */}
      <div className="space-y-1">
        <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">{t('mesures.profile_name_label')}</div>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)}
               placeholder={suggested} maxLength={NAME_MAXLEN}
               className="w-full bg-sunken rounded px-2.5 py-1.5 font-mono text-xs2 text-text-strong
                          border border-border-soft focus:outline-none focus:border-accent"/>
        {/* ALL name feedback in one place (below the field), readable messages:
            accent refusal (red = error) > collision/length pushed by the parent (amber) >
            limit reached (faint = info, the block at 63 is not mysterious). */}
        {!nameAscii ? (
          <p className="text-tiny text-danger">{t('mesures.profile_name_ascii_only')}</p>
        ) : nameNotice ? (
          <p className="text-tiny text-warn">{nameNotice}</p>
        ) : name.length >= NAME_MAXLEN ? (
          <p className="text-tiny text-text-faint">{t('mesures.profile_name_maxlen')}</p>
        ) : null}
      </div>

      {/* Profile base — when >=2 scans included */}
      <div className="space-y-2">
        <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">{t('scan.base.section_title')}</div>
        {nIncluded >= 2 ? (
          <>
            <Segmented value={base} onChange={setBase} options={[
              { value: 'average', label: t('scan.base.average', { n: nIncluded }) },
              { value: 'last', label: t('scan.base.last') },
            ]}/>
            <p className="text-tiny text-text-faint leading-relaxed">
              <strong>{t('scan.base.average_recommended_bold')}</strong> {t('scan.base.average_recommended_mid', { n: nIncluded })} <strong>{t('scan.base.average_recommended_bold2')}</strong>{t('scan.base.average_recommended_end')}
            </p>
          </>
        ) : (
          <p className="text-tiny text-text-faint">{t('scan.base.single_kept')}</p>
        )}
      </div>

      {/* Additional sources — same paper+GE; current chart included implicitly */}
      {hasSources && (
        <div className="space-y-1.5 pt-2 border-t border-border-soft">
          <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">{t('mesures.sources_section')}</div>
          {/* Bounded list (max-h + scroll) — title + separator stay FIXED above;
              safety net for the "many sources" case (Rebuild stays within reach just below). */}
          <div className="space-y-1.5 max-h-[200px] overflow-y-auto">
          {(sources.extra_charts || []).map((c) => (
            <label key={c.chart_id} className="flex items-center gap-2 cursor-pointer text-xs2 text-text-strong">
              <input type="checkbox" checked={selCharts.has(c.chart_id)}
                     onChange={() => toggleChart(c.chart_id)} className="accent-accent"/>
              <span className="flex-1 font-mono truncate" title={c.chart_id}>{c.chart_id}</span>
              <span className="text-tiny text-text-faint flex-shrink-0">
                {t('mesures.scans_count', { count: c.n_scans })}
                {c.n_rejected > 0 && (
                  <span className="text-text-faint"> · {t('mesures.readings_rejected', { count: c.n_rejected })}</span>
                )}
              </span>
            </label>
          ))}
          {(sources.profiles || []).map((p) => (
            <label key={p.path} className="flex items-center gap-2 cursor-pointer text-xs2 text-text-strong">
              <input type="checkbox" checked={selProfiles.has(p.path)}
                     onChange={() => toggleProfile(p.path)} className="accent-accent"/>
              <span className="flex-1 truncate" title={p.path}>{p.label}</span>
              <span className="text-tiny text-text-faint flex-shrink-0">{t('mesures.source_profile_tag')}</span>
            </label>
          ))}
          </div>
          <p className="text-tiny text-text-faint leading-snug">{t('mesures.sources_hint')}</p>
        </div>
      )}

      {/* colprof options — free line (pre-filled at the settled default, overridable) + presets + help */}
      <div className="space-y-2 pt-2 border-t border-border-soft">
        <span className="text-xs2 text-text-muted">{t('chart_create.colprof_line')}</span>
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-xs2 bg-sunken-deep text-text-muted px-2 py-1 rounded">colprof</span>
          <input value={colprofFlags} onChange={(e) => setColprofFlags(e.target.value)}
                 placeholder="-v -qh -S AdobeRGB1998"
                 className="flex-1 bg-sunken rounded px-2.5 py-1.5 font-mono text-xs2 text-text-strong"/>
        </div>
        <div className="flex items-center gap-3 flex-wrap text-xs2">
          {colprofPresets.length > 0 && (
            <_Select value=""
              onChange={(k) => { const p = colprofPresets.find((x) => x.name === k); if (p) setColprofFlags(p.flags); }}
              options={[{ value: '', label: t('chart_create.apply_preset') },
                ...colprofPresets.map((p) => ({ value: p.name, label: p.label || p.name }))]}/>
          )}
          {loadColprofHelp && (
            <button type="button" onClick={loadColprofHelp}
                    className="flex items-center gap-1 text-accent hover:underline">
              <HelpCircle size={13}/>{t('chart_create.colprof_help')}
            </button>
          )}
          {saveColprofPreset && (
            <span className="flex items-center gap-1">
              <input value={colprofPresetKey} onChange={(e) => setColprofPresetKey(e.target.value)}
                     placeholder={t('chart_create.preset_name')}
                     className="w-28 bg-sunken rounded px-2 py-1 text-text-strong"/>
              <button type="button" onClick={saveColprofPreset} disabled={!colprofPresetKey?.trim()}
                      className="flex items-center gap-1 text-text-muted hover:text-text-strong disabled:opacity-40">
                <Save size={13}/>{t('chart_create.save_preset')}
              </button>
            </span>
          )}
        </div>
        {showColprofHelp && (
          <pre className="max-h-[200px] overflow-auto bg-sunken/60 border border-border-soft rounded p-2 text-tiny font-mono whitespace-pre-wrap">{colprofHelp || '…'}</pre>
        )}
      </div>

      {/* VISIBILITY: the command that WILL be run (no more opaque click) */}
      <div className="text-tiny text-text-faint">
        {t('mesures.build_will_run')}{' '}
        <code className="font-mono text-text-muted break-all">colprof {colprofFlags}</code>
      </div>

      {onBuild && (
        <button type="button" onClick={() => onBuild()} disabled={busy}
                className="inline-flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press disabled:opacity-50">
          {buildPhase
            ? <><Loader2 size={13} className="animate-spin"/> {t('mesures.building')}</>
            : <><RefreshCw size={13}/> {buildLabel}</>}
        </button>
      )}
    </div>
  );
}
