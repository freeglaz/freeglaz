import { useEffect, useRef, useState } from 'react';
import { Download, RotateCcw, Upload, History, Palette, SearchCheck, PackagePlus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import * as api from '../../../api/client.js';
import { errorText } from '../../../lib/errorText.js';
import { saveFromUrl } from '../../../lib/fileIO.js';
import { useIccBackups } from '../../../hooks/useIccBackups.js';
import { useLoadedPaper } from '../../../hooks/useLoadedPaper.js';
import ConfirmModal from '../../ui/ConfirmModal.jsx';
import Badge from '../../ui/Badge.jsx';
import ProfileInspectorModal from '../../ProfileInspector/ProfileInspectorModal.jsx';
import InstallFromRepoModal from './InstallFromRepoModal.jsx';

/**
 * An ICC profile card (P1.D + P2.C — spec §5 Zone 2).
 * Destructive modals with frozen wording (cf P2 brief). Toasts via the
 * ``onNotice(kind, message)`` prop exposed by the parent (pattern).
 */
export default function IccSlot({ slot, mediaid, paperFactory = false, paperName, autoOpenInstall = false, onChanged, onNotice, onProfile, offline = false }) {
  const { t, i18n } = useTranslation();
  // #5 — offline: actions requiring the Z9 live (export/install/
  // replace/restore/rollback/profile) are disabled with a tooltip. The inspector
  // (local file reading) stays active.
  const offlineTitle = offline ? t('papers.icc.action_offline') : undefined;
  const loadedPaper = useLoadedPaper();
  const isLoaded = loadedPaper?.mediaid === mediaid;
  const title = _slotTitle(slot.kind, t);
  const fileInputRef = useRef(null);

  const [confirmKind, setConfirmKind] = useState(null);
  const [pendingFile, setPendingFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);

  const geState = _geStateFromKind(slot.kind);
  const backups = useIccBackups(mediaid, geState);
  const hasBackup = backups.count > 0;

  useEffect(() => {
    if (autoOpenInstall && !paperFactory) setInstallOpen(true);
  }, [autoOpenInstall, paperFactory]);

  const handleExport = async () => {
    // Fetch the ICC then save via the desktop-aware path — never let the webview
    // navigate to the attachment URL (freezes the desktop app, #22).
    try {
      const saved = await saveFromUrl(
        api.paperIccDownloadUrl(mediaid, geState),
        `${(paperName || mediaid)}_${geState}.icc`,
        'application/vnd.iccprofile');
      if (saved) onNotice?.('success', t('papers.icc.toast_export_success'));
    } catch (e) {
      onNotice?.('error', t('papers.icc.toast_export_failed', { message: errorText(e, t) }));
    }
  };

  const handleReplaceClick = () => { fileInputRef.current?.click(); };

  const handleFileChosen = (e) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f) return;
    setPendingFile(f);
    setConfirmKind('replace');
  };

  const handleConfirmReplace = async () => {
    if (!pendingFile) return;
    setBusy(true);
    try {
      await api.replacePaperIcc(mediaid, geState, pendingFile);
      onNotice?.('success', t('papers.icc.toast_replace_success'));
      await backups.refresh();
      onChanged?.();
    } catch (e) {
      onNotice?.('error', t('papers.icc.toast_replace_failed', { message: errorText(e, t) }));
    } finally {
      setBusy(false);
      setConfirmKind(null);
      setPendingFile(null);
    }
  };

  const handleConfirmRestore = async () => {
    setBusy(true);
    try {
      await api.restoreFactoryIcc(mediaid, geState);
      onNotice?.('success', t('papers.icc.toast_restore_success'));
      await backups.refresh();
      onChanged?.();
    } catch (e) {
      onNotice?.('error', t('papers.icc.toast_restore_failed', { message: e.message }));
    } finally {
      setBusy(false);
      setConfirmKind(null);
    }
  };

  const handleConfirmRollback = async () => {
    setBusy(true);
    try {
      await api.rollbackPaperIcc(mediaid, geState);
      onNotice?.('success', t('papers.icc.toast_rollback_success'));
      await backups.refresh();
      onChanged?.();
    } catch (e) {
      onNotice?.('error', t('papers.icc.toast_rollback_failed', { message: e.message }));
    } finally {
      setBusy(false);
      setConfirmKind(null);
    }
  };

  const rollbackDate = backups.latest ? _fmtBackupDate(backups.latest, i18n.language) : null;
  const replaceMessage = pendingFile
    ? `${t('papers.icc.modal_replace_message')}\n\n${t('papers.icc.modal_replace_file_line', { filename: pendingFile.name })}`
    : t('papers.icc.modal_replace_message');

  return (
    <div className="card flex-1 min-w-0 p-3.5">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted">
          {title}
        </span>
        <Badge kind={slot.custom ? 'info' : 'neutral'}>
          {slot.custom ? t('papers.icc.badge_custom') : t('papers.icc.badge_factory')}
        </Badge>
      </div>
      <p
        className="text-[12.5px] font-mono break-all leading-snug text-text-strong mb-1.5"
        title={slot.name}>
        {slot.name || <span className="text-text-faint italic">{t('papers.icc.unnamed')}</span>}
      </p>
      {slot.date && (
        <p className="text-tiny text-text-faint mb-3">
          {t('papers.icc.issued_on', { date: _fmtDate(slot.date, i18n.language) })}
        </p>
      )}
      <div className="flex items-center gap-1 -mx-1 flex-wrap">
        <IccAction icon={SearchCheck} label={t('papers.icc.action_inspect')}
          onClick={() => setInspectorOpen(true)} disabled={busy}/>
        <IccAction icon={Download} label={t('papers.icc.action_export')}
          onClick={handleExport} disabled={busy || offline} disabledTitle={offlineTitle}/>
        {!paperFactory && (
          <IccAction icon={PackagePlus} label={t('papers.icc.action_install')}
            onClick={() => setInstallOpen(true)} disabled={busy || offline} disabledTitle={offlineTitle}/>
        )}
        {!paperFactory && (
          <IccAction icon={Upload} label={t('papers.icc.action_replace')}
            onClick={handleReplaceClick} disabled={busy || offline} disabledTitle={offlineTitle}/>
        )}
        {!paperFactory && slot.custom && (
          <IccAction icon={RotateCcw} label={t('papers.icc.action_restore_factory')}
            onClick={() => setConfirmKind('restore')} disabled={busy || offline} disabledTitle={offlineTitle}/>
        )}
        {!paperFactory && hasBackup && (
          <IccAction icon={History} label={t('papers.icc.action_rollback')}
            onClick={() => setConfirmKind('rollback')} disabled={busy || offline} disabledTitle={offlineTitle}/>
        )}
        {!paperFactory && onProfile && (
          <IccAction icon={Palette}
            label={slot.custom ? t('wizard_profile_paper.entry_reprofile_slot') : t('wizard_profile_paper.entry_profile_slot')}
            onClick={(isLoaded && !offline) ? onProfile : undefined}
            disabled={busy || !isLoaded || offline}
            disabledTitle={offline ? offlineTitle
              : (!isLoaded ? t('papers.action_load_this_to_profile') : undefined)}/>
        )}
      </div>
      {paperFactory && (
        <p className="mt-2 text-tiny text-text-faint leading-snug">
          {t('papers.icc.factory_locked_note')}
        </p>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept=".icc,.icm,application/vnd.iccprofile"
        onChange={handleFileChosen}
        className="hidden"/>

      <ConfirmModal
        open={confirmKind === 'replace'}
        title={t('papers.icc.modal_replace_title')}
        message={replaceMessage}
        confirmLabel={busy ? t('papers.icc.modal_replace_in_progress') : t('papers.icc.modal_replace_confirm')}
        cancelLabel={t('common.cancel')}
        confirmKind="primary"
        onConfirm={handleConfirmReplace}
        onCancel={() => { setConfirmKind(null); setPendingFile(null); }}/>

      <ConfirmModal
        open={confirmKind === 'restore'}
        title={t('papers.icc.modal_restore_title')}
        message={t('papers.icc.modal_restore_message')}
        confirmLabel={busy ? t('papers.icc.modal_restore_in_progress') : t('papers.icc.modal_restore_confirm')}
        cancelLabel={t('common.cancel')}
        confirmKind="primary"
        onConfirm={handleConfirmRestore}
        onCancel={() => setConfirmKind(null)}/>

      <ConfirmModal
        open={confirmKind === 'rollback'}
        title={t('papers.icc.modal_rollback_title')}
        message={rollbackDate
          ? t('papers.icc.modal_rollback_message', { date: rollbackDate })
          : t('papers.icc.modal_rollback_no_date')}
        confirmLabel={busy ? t('papers.icc.modal_rollback_in_progress') : t('papers.icc.modal_rollback_confirm')}
        cancelLabel={t('common.cancel')}
        confirmKind="primary"
        onConfirm={handleConfirmRollback}
        onCancel={() => setConfirmKind(null)}/>

      <ProfileInspectorModal
        open={inspectorOpen}
        onClose={() => setInspectorOpen(false)}
        source={{ paperMediaid: mediaid, slot: geState }}/>

      <InstallFromRepoModal
        open={installOpen}
        onClose={() => setInstallOpen(false)}
        mediaid={mediaid}
        paperName={paperName}
        geState={geState}
        slotTitle={title}
        currentProfileName={slot.name}
        onNotice={onNotice}
        onInstalled={async () => { await backups.refresh(); onChanged?.(); }}/>
    </div>
  );
}


function IccAction({ icon: Icon, label, onClick, disabled, disabledTitle }) {
  const btn = (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={disabled ? undefined : label}
      aria-label={label}
      className={`
        flex items-center gap-1 px-2 py-1 rounded text-tiny font-medium transition-colors
        ${disabled
          ? 'text-text-faint opacity-50 cursor-not-allowed'
          : 'text-text-muted hover:text-text-strong hover:bg-sunken'}
      `}>
      <Icon size={11} strokeWidth={2} aria-hidden="true"/>
      {label}
    </button>
  );
  // A <button disabled> does NOT trigger a native tooltip (the `title` is ignored):
  // we wrap it in a <span title> (which does receive the hover) to explain the
  // disabling CONDITION (e.g. "Re-profile": load the paper / Z9 unreachable).
  // Same wrapper pattern as the capability icons.
  if (disabled && disabledTitle) {
    return <span title={disabledTitle} className="inline-flex">{btn}</span>;
  }
  return btn;
}


function _slotTitle(kind, t) {
  const key = {
    ge_off: 'papers.icc.slot_title_ge_off',
    ge_on:  'papers.icc.slot_title_ge_on',
    single: 'papers.icc.slot_title_single',
  }[kind];
  return key ? t(key) : t('papers.icc.slot_title_single');
}


function _geStateFromKind(kind) {
  return { ge_off: 'off', ge_on: 'on', single: 'single' }[kind] || 'single';
}


function _fmtDate(iso, lang) {
  if (!iso) return '';
  const locale = lang === 'en' ? 'en-US' : 'fr-FR';
  try {
    return new Intl.DateTimeFormat(locale, {
      year: 'numeric', month: 'long', day: 'numeric',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}


function _fmtBackupDate(iso, lang) {
  if (!iso) return '';
  const locale = lang === 'en' ? 'en-US' : 'fr-FR';
  try {
    return new Intl.DateTimeFormat(locale, {
      year: 'numeric', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}
