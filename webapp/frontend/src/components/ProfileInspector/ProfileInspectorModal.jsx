import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { X, GitCompare } from 'lucide-react';
import * as api from '../../api/client.js';
import OriginBadge, { originKeyFromInspection } from './OriginBadge.jsx';
import TRCBadge from './TRCBadge.jsx';
import ConformanceBadge from './ConformanceBadge.jsx';
import IdentityView from './IdentityView.jsx';
import View3D from './View3D.jsx';
import Slice2DView from './Slice2DView.jsx';
import MappingView from './MappingView.jsx';

/**
 * Full-screen ICC profile inspection modal.
 *
 * 5 sidebar tabs: Identity / Internals / 3D View / 2D Slices /
 * Mapping (all enabled from Mapping onward).
 */
const TABS = [
  { id: 'identity',  enabled: true,  i18n_key: 'inspector.tab_identity'  },
  { id: 'view3d',    enabled: true,  i18n_key: 'inspector.tab_view3d'    },
  { id: 'slice2d',   enabled: true,  i18n_key: 'inspector.tab_slice2d'   },
  { id: 'mapping',   enabled: true,  i18n_key: 'inspector.tab_mapping'   },
];


export default function ProfileInspectorModal({
  open, onClose, source, onCompare,
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState('identity');
  const [inspection, setInspection] = useState(null);
  const [error, setError] = useState(null);
  // Shared state for the 3D view selector: lets popovers
  // seed a specific entry via the "Visualize in 3D" button.
  const [view3dEntry, setView3dEntry] = useState(null);

  const openView3D = useCallback((entryKey) => {
    setView3dEntry(entryKey);
    setTab('view3d');
  }, []);

  useEffect(() => {
    if (!open) return;
    const h = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose?.();
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || !source) return;
    setTab('identity');
    setInspection(null);
    setError(null);
    let cancelled = false;
    api.inspectProfile(source)
      .then((data) => { if (!cancelled) setInspection(data); })
      .catch((e) => { if (!cancelled) setError(e.message || String(e)); });
    return () => { cancelled = true; };
  }, [open, source?.path, source?.paperMediaid, source?.slot]);

  const handleClose = useCallback(() => onClose?.(), [onClose]);

  if (!open) return null;

  // Portal to document.body: the modal must escape any
  // containing block created by an ancestor (transform/filter/will-change).
  // In particular PaperDetailPanel uses animate-slidein which leaves a
  // residual transform — without the portal, fixed inset-0 positions relative
  // to the panel (640px) instead of the viewport.
  return createPortal(
    <div
      className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-[2px]
                 flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t('inspector.title')}
      onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}>
      <div
        className="bg-bg border border-border-soft rounded-[14px] shadow-2xl
                   w-[1280px] max-w-[90vw] h-[820px] max-h-[90vh]
                   flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}>
        <Header
          inspection={inspection}
          onClose={handleClose}
          onCompare={onCompare && source?.path
            ? () => onCompare(source.path)
            : null}/>
        <div className="flex-1 flex min-h-0">
          <Sidebar tab={tab} setTab={setTab}/>
          <main className="flex-1 overflow-y-auto bg-sunken-deep">
            {error
              ? <ErrorState message={error}/>
              : tab === 'identity'
                ? <IdentityView inspection={inspection} source={source} onView3D={openView3D}/>
                : tab === 'view3d'
                  ? <View3D inspection={inspection} source={source} initialEntry={view3dEntry}/>
                  : tab === 'slice2d'
                    ? <Slice2DView source={source}/>
                    : tab === 'mapping'
                      ? <MappingView source={source}/>
                      : null}
          </main>
        </div>
      </div>
    </div>,
    document.body,
  );
}


function Header({ inspection, onClose, onCompare }) {
  const { t } = useTranslation();
  const originKey = inspection ? originKeyFromInspection(inspection) : 'unknown';
  const trcFamily = inspection?.trc?.family?.effective ?? 'unknown_custom';
  return (
    <div className="px-6 pt-5 pb-4 flex items-start gap-4 border-b border-border-soft">
      <div className="flex-1 min-w-0">
        <div className="text-[10px] font-bold tracking-[0.10em] uppercase text-accent mb-1 font-mono">
          {t('inspector.title')}
        </div>
        <h2 className="text-[18px] font-semibold text-text-strong tracking-[-0.01em] truncate"
            title={inspection?.description}>
          {inspection?.description || t('inspector.loading')}
        </h2>
      </div>
      {inspection && (
        <div className="flex items-center gap-2 mt-1 flex-shrink-0 flex-wrap justify-end">
          <OriginBadge originKey={originKey}/>
          <TRCBadge family={trcFamily}/>
          {inspection.conformance && (
            <ConformanceBadge conformance={inspection.conformance}/>
          )}
        </div>
      )}
      {onCompare && (
        <button
          type="button"
          onClick={onCompare}
          title={t('inspector.action_compare')}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs2
                     border border-border-soft hover:border-border-strong
                     text-text-muted hover:text-text-strong rounded-md
                     transition-colors flex-shrink-0">
          <GitCompare size={13}/>
          {t('inspector.action_compare')}
        </button>
      )}
      <button
        type="button"
        onClick={onClose}
        title={t('common.close')}
        className="w-8 h-8 rounded-md flex items-center justify-center text-text-muted
                   hover:text-text-strong hover:bg-sunken transition-colors flex-shrink-0">
        <X size={15} strokeWidth={1.8}/>
      </button>
    </div>
  );
}


function Sidebar({ tab, setTab }) {
  const { t } = useTranslation();
  return (
    <nav className="w-[180px] border-r border-border-soft bg-surface p-3 flex flex-col gap-0.5"
         aria-label={t('inspector.sidebar_aria')}>
      {TABS.map((entry) => {
        const active = tab === entry.id;
        const title = entry.enabled ? '' : t('inspector.tab_disabled_tooltip');
        return (
          <button
            key={entry.id}
            type="button"
            onClick={entry.enabled ? () => setTab(entry.id) : undefined}
            disabled={!entry.enabled}
            title={title}
            className={`
              px-3 py-2 text-left text-xs2 rounded-md transition-colors
              ${active
                ? 'bg-accent/10 text-accent font-semibold'
                : entry.enabled
                  ? 'text-text-muted hover:text-text-strong hover:bg-sunken'
                  : 'text-text-faint opacity-50 cursor-not-allowed'}
            `}>
            {t(entry.i18n_key)}
          </button>
        );
      })}
    </nav>
  );
}


function ErrorState({ message }) {
  const { t } = useTranslation();
  return (
    <div className="px-6 py-8">
      <h4 className="text-amber text-sm font-semibold mb-2">
        {t('inspector.error_title')}
      </h4>
      <p className="text-text-muted text-xs2 font-mono">{message}</p>
    </div>
  );
}
