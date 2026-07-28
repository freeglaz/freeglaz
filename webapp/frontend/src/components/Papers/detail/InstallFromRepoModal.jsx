import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, PackageCheck, ArrowRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import * as api from '../../../api/client.js';
import { geLabel } from '../../../lib/geLabel.js';

const LEVELS = ['slot', 'paper', 'all'];

function _glossMatches(geState, gloss) {
  const g = String(gloss || '').toUpperCase();
  if (geState === 'on') return g === 'FULLPAGE' || g === 'ON';
  return g === 'OFF' || g === 'UNKNOWN';
}

function _flatten(tree) {
  const out = [];
  for (const s of tree?.serials || []) {
    for (const paper of s.papers || []) {
      for (const prof of paper.profiles || []) out.push(prof);
    }
  }
  return out;
}

export default function InstallFromRepoModal({
  open, onClose, mediaid, paperName, geState, slotTitle,
  currentProfileName, onInstalled, onNotice,
}) {
  const { t } = useTranslation();
  const [level, setLevel] = useState('slot');
  const [tree, setTree] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLevel('slot');
    setPicked(null);
    setBusy(false);
    setLoadError(null);
    setLoading(true);
    let alive = true;
    api.getZ9Profiles()
      .then((d) => { if (alive) setTree(d); })
      .catch((e) => { if (alive) setLoadError(e.message || String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const h = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); if (!busy) onClose?.(); }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, busy, onClose]);

  const all = useMemo(() => _flatten(tree), [tree]);
  const list = useMemo(() => {
    if (level === 'all') return all;
    const samePaper = all.filter((e) => (e.media_id || e.mediaId) === mediaid);
    if (level === 'paper') return samePaper;
    return samePaper.filter((e) => _glossMatches(geState, e.gloss_slot));
  }, [all, level, mediaid, geState]);

  const install = async () => {
    if (!picked) return;
    setBusy(true);
    try {
      const url = api.z9ProfileExportUrl({
        serial: picked.serial,
        mediaId: picked.media_id || picked.mediaId,
        filename: picked.filename,
      });
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const file = new File([blob], picked.filename, { type: 'application/vnd.iccprofile' });
      await api.replacePaperIcc(mediaid, geState, file);
      onNotice?.('success', t('papers.icc.toast_install_success'));
      onInstalled?.();
      onClose?.();
    } catch (e) {
      onNotice?.('error', t('papers.icc.toast_install_failed', { message: e.message }));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={() => { if (!busy) onClose?.(); }}
      role="dialog"
      aria-modal="true">
      <div
        className="bg-surface border border-border-soft rounded-xl shadow-xl w-[560px] max-w-[92vw] max-h-[86vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between px-5 pt-4 pb-3 border-b border-border-soft">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-text-strong tracking-tight">
              {t('papers.icc.install_title')}
            </h2>
            <p className="text-tiny text-text-faint mt-0.5 truncate">
              {t('papers.icc.install_target', { paper: paperName || mediaid, slot: slotTitle })}
            </p>
          </div>
          <button type="button" onClick={() => { if (!busy) onClose?.(); }}
            aria-label={t('common.cancel')}
            className="p-1 -mr-1 text-text-muted hover:text-text-strong rounded">
            <X size={16}/>
          </button>
        </div>

        <div className="px-5 pt-3">
          <div className="inline-flex rounded-md border border-border-soft overflow-hidden text-xs2">
            {LEVELS.map((lv) => (
              <button
                key={lv}
                type="button"
                onClick={() => { setLevel(lv); setPicked(null); }}
                className={`px-3 py-1.5 font-medium transition-colors ${
                  level === lv
                    ? 'bg-accent text-white'
                    : 'text-text-muted hover:text-text-strong hover:bg-sunken'
                }`}>
                {t(`papers.icc.install_filter_${lv}`)}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-3 min-h-[120px]">
          {loading ? (
            <p className="text-xs2 text-text-faint italic py-4">{t('papers.icc.install_loading')}</p>
          ) : loadError ? (
            <p className="text-xs2 text-danger py-4">{loadError}</p>
          ) : list.length === 0 ? (
            <p className="text-xs2 text-text-faint italic py-4">{t('papers.icc.install_empty')}</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {list.map((e) => {
                const sel = picked
                  && picked.filename === e.filename
                  && (picked.media_id || picked.mediaId) === (e.media_id || e.mediaId)
                  && picked.serial === e.serial;
                return (
                  <li key={`${e.serial}-${e.media_id || e.mediaId}-${e.filename}`}>
                    <button
                      type="button"
                      onClick={() => setPicked(e)}
                      className={`w-full text-left px-3 py-2 rounded-md border transition-colors ${
                        sel
                          ? 'border-accent bg-accent/10'
                          : 'border-border-soft hover:border-border-strong hover:bg-sunken/50'
                      }`}>
                      <div className="flex items-center gap-2">
                        <span className="text-xs2 font-medium text-text-strong truncate">
                          {e.label || e.filename}
                        </span>
                        <span className="text-[9px] font-semibold tracking-wider uppercase px-1.5 py-px rounded bg-sunken-deep text-text-muted flex-shrink-0">
                          {t('papers.icc.install_meta_slot', { slot: e.gloss_slot ? geLabel(e.gloss_slot) : '—' })}
                        </span>
                      </div>
                      <div className="text-tiny text-text-faint mt-0.5 truncate">
                        {e.paper_name || (e.media_id || e.mediaId)}
                        {e.method_flags ? ` · ${e.method_flags}` : ''}
                        {e.n_patches ? ` · ${e.n_patches} patches` : ''}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="px-5 py-3 border-t border-border-soft">
          {picked && (
            <div className="mb-3 text-xs2 text-text-muted leading-relaxed">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-text-faint">{t('papers.icc.install_confirm_before')}</span>
                <span className="font-mono text-text-strong break-all">
                  {currentProfileName || t('papers.icc.unnamed')}
                </span>
              </div>
              <div className="flex items-center gap-2 flex-wrap mt-1">
                <ArrowRight size={12} className="text-accent flex-shrink-0"/>
                <span className="text-text-faint">{t('papers.icc.install_confirm_after')}</span>
                <span className="font-mono text-accent break-all">{picked.label || picked.filename}</span>
              </div>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => { if (!busy) onClose?.(); }}
              className="px-3.5 py-2 rounded-md text-sm font-medium text-text-strong bg-sunken hover:bg-sunken-deep transition-colors">
              {t('common.cancel')}
            </button>
            <button type="button" onClick={install} disabled={!picked || busy}
              className="px-3.5 py-2 rounded-md text-sm font-semibold text-white bg-accent hover:bg-accent-press transition-colors disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5">
              <PackageCheck size={14}/>
              {busy ? t('papers.icc.install_in_progress') : t('papers.icc.install_confirm_btn')}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
