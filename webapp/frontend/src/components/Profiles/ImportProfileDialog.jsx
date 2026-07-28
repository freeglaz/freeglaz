import { useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { X, Upload } from 'lucide-react';
import { errorText } from '../../lib/errorText.js';

/**
 * Dialog for importing an ICC file into the repo (17.2).
 *
 * Fields: category (printers/displays/workingspaces), device (if printers),
 * .icc file, optional display_name. Submits via POST /api/profiles/repo
 * multipart.
 */
export default function ImportProfileDialog({ onClose, onUploaded }) {
  const { t } = useTranslation();
  const [category, setCategory] = useState('workingspaces');
  const [device, setDevice] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!file) { setError(t('profils.import_file_required')); return; }
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append('file', file);
    form.append('category', category);
    if (category === 'printers') form.append('device', device);
    if (displayName) form.append('display_name', displayName);
    try {
      const r = await fetch('/api/profiles/repo', { method: 'POST', body: form });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const err = new Error(
          (data.detail && typeof data.detail === 'object')
            ? (data.detail.message || `HTTP ${r.status}`)
            : (data.detail || `HTTP ${r.status}`));
        err.detail = data.detail;
        throw err;
      }
      onUploaded?.(data);
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-[2px]
                 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={t('profils.import_title')}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <form onSubmit={submit}
            className="bg-bg border border-border-soft rounded-lg shadow-xl
                       w-[480px] max-w-[90vw] max-h-[85vh] overflow-y-auto">
        <div className="px-5 py-3 border-b border-border-soft flex items-center gap-2">
          <h2 className="text-sm font-semibold text-text-strong flex-1">
            {t('profils.import_title')}
          </h2>
          <button type="button" onClick={onClose}
                  className="w-7 h-7 rounded-md flex items-center justify-center
                             text-text-muted hover:text-text-strong hover:bg-sunken
                             transition-colors">
            <X size={14}/>
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <label className="block">
            <span className="block text-xs2 font-medium text-text-strong mb-1">
              {t('profils.import_category')}
            </span>
            <select value={category} onChange={(e) => setCategory(e.target.value)}
                    className="w-full px-2 py-1.5 text-xs2 bg-sunken/40
                               border border-border-soft rounded
                               focus:outline-none focus:border-accent">
              <option value="workingspaces">{t('profils.category_workingspaces')}</option>
              <option value="displays">{t('profils.category_displays')}</option>
              <option value="printers">{t('profils.category_printers')}</option>
            </select>
          </label>

          {category === 'printers' && (
            <label className="block">
              <span className="block text-xs2 font-medium text-text-strong mb-1">
                {t('profils.import_device')}
              </span>
              <input type="text" value={device}
                     onChange={(e) => setDevice(e.target.value)}
                     placeholder="Epson-P900"
                     pattern="[A-Za-z0-9._-]+"
                     required={category === 'printers'}
                     className="w-full px-2 py-1.5 text-xs2 bg-sunken/40
                                border border-border-soft rounded
                                focus:outline-none focus:border-accent"/>
              <span className="block text-tiny text-text-faint mt-1">
                {t('profils.import_device_hint')}
              </span>
            </label>
          )}

          <label className="block">
            <span className="block text-xs2 font-medium text-text-strong mb-1">
              {t('profils.import_file')}
            </span>
            <input type="file" accept=".icc"
                   onChange={(e) => setFile(e.target.files?.[0] || null)}
                   required
                   className="w-full text-xs2"/>
            {file && (
              <span className="block text-tiny text-text-faint mt-1 font-mono">
                {file.name} · {(file.size / 1024).toFixed(0)} KB
              </span>
            )}
          </label>

          <label className="block">
            <span className="block text-xs2 font-medium text-text-strong mb-1">
              {t('profils.import_display_name')}
            </span>
            <input type="text" value={displayName}
                   onChange={(e) => setDisplayName(e.target.value)}
                   placeholder={file?.name?.replace(/\.icc$/i, '') || 'Rec2020'}
                   className="w-full px-2 py-1.5 text-xs2 bg-sunken/40
                              border border-border-soft rounded
                              focus:outline-none focus:border-accent"/>
          </label>

          {error && (
            <div className="text-xs2 text-danger bg-danger/10 border border-danger/30
                            rounded px-2 py-1.5 font-mono">
              {error}
            </div>
          )}
        </div>

        <div className="px-5 py-3 border-t border-border-soft bg-sunken/30
                        flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy}
                  className="px-3 py-1.5 text-xs2 border border-border-soft
                             rounded text-text-muted hover:text-text-strong
                             hover:bg-sunken transition-colors disabled:opacity-50">
            {t('common.cancel')}
          </button>
          <button type="submit" disabled={busy || !file}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs2
                             bg-accent/10 text-accent rounded font-medium
                             hover:bg-accent/15 transition-colors
                             disabled:opacity-50 disabled:cursor-not-allowed">
            <Upload size={12}/>
            {busy ? t('profils.import_in_progress') : t('profils.import_submit')}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
