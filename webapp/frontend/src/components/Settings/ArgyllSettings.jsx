import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { getArgyllSettings, updateArgyllRoot } from '../../api/client.js';

/**
 * Argyll settings — detected state + a manual root field.
 *
 * Persists to ~/.freeglazrc.toml [argyll] (the store the resolution cascade
 * reads), NOT the app-level settings.json. The state shown is `check_argyll()`
 * (same source as `freeglaz check` and the warning banner), re-probed after a
 * save so it refreshes without a restart. If a FREEGLAZ_ARGYLL_* env var is set
 * it overrides the GUI → the field is disabled and a note explains it.
 */
export default function ArgyllSettings() {
  const { t } = useTranslation();
  const [data, setData] = useState(null);   // { status, root, env_override }
  const [root, setRoot] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);   // success confirmation (saved / reset)

  useEffect(() => {
    getArgyllSettings()
      .then((d) => { setData(d); setRoot(d.root || ''); })
      .catch(() => setError(t('settings.argyll.load_error')))
      .finally(() => setLoading(false));
  }, [t]);

  const save = async () => {
    const candidate = root.trim();   // empty = reset to auto-detection (valid intent)
    setSaving(true); setError(null); setNotice(null);
    try {
      const d = await updateArgyllRoot(candidate);
      setData(d); setRoot(d.root || '');
      // Confirm which of the two things happened: a custom root was set, or the
      // path was cleared → auto-detection. The refreshed badge shows the result.
      setNotice(d.root ? t('settings.argyll.saved') : t('settings.argyll.reset'));
    } catch (e) {
      const code = e.detail?.code;
      if (code === 'argyll_root_invalid') {
        setError(t('settings.argyll.invalid', { missing: (e.detail.missing || []).join(', ') }));
      } else {
        setError(e.message || t('settings.argyll.save_error'));
      }
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="text-sm text-text-muted">…</div>;

  const s = data?.status;
  const env = data?.env_override;

  return (
    <div className="space-y-4">
      {/* Detected state */}
      <div className="flex items-start gap-2.5">
        {s?.ok
          ? <CheckCircle2 size={16} className="text-success mt-0.5 flex-shrink-0"/>
          : <AlertTriangle size={16} className="text-warn mt-0.5 flex-shrink-0"/>}
        <div className="min-w-0">
          <div className="text-sm font-medium text-text-strong">
            {s?.ok ? t('settings.argyll.available') : t('settings.argyll.incomplete')}
          </div>
          {!s?.ok && s?.missing?.length > 0 && (
            <div className="text-xs text-text-muted mt-0.5">
              {t('settings.argyll.missing', { missing: s.missing.join(', ') })}
            </div>
          )}
          {s?.bin_dir && (
            <div className="text-xs2 font-mono text-text-faint mt-1 break-all">bin: {s.bin_dir}</div>
          )}
          {s?.ref_dir && (
            <div className="text-xs2 font-mono text-text-faint break-all">ref: {s.ref_dir}</div>
          )}
        </div>
      </div>

      {/* Env override note */}
      {env && (
        <div className="text-xs text-text-muted bg-sunken rounded-md px-3 py-2 leading-relaxed">
          {t('settings.argyll.env_override')}
        </div>
      )}

      {/* Manual root */}
      <div>
        <label className="block text-xs2 text-text-muted mb-1.5">{t('settings.argyll.root_label')}</label>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={root}
            onChange={(e) => { setRoot(e.target.value); setNotice(null); }}
            disabled={env || saving}
            placeholder={t('settings.argyll.root_placeholder')}
            className="flex-1 bg-sunken px-3 py-2 rounded-md text-sm font-mono text-text-strong
                       outline-none disabled:opacity-50"/>
          <button
            type="button"
            onClick={save}
            disabled={env || saving}
            className="btn-secondary text-sm px-3.5 py-2 disabled:opacity-40 disabled:cursor-not-allowed">
            {saving ? t('settings.argyll.saving') : t('common.save')}
          </button>
        </div>
        <p className="text-xs text-text-faint mt-1.5 leading-relaxed">{t('settings.argyll.root_hint')}</p>
      </div>

      {error && (
        <div className="text-xs text-danger bg-danger/10 border border-danger/30 rounded-md px-3 py-2">
          {error}
        </div>
      )}
      {notice && !error && (
        <div className="text-xs text-success bg-success/10 border border-success/30 rounded-md px-3 py-2">
          {notice}
        </div>
      )}
    </div>
  );
}
