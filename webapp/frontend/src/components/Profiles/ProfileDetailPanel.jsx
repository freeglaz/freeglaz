import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  X, Search as SearchIcon, Copy, Trash2, FolderInput, Lock, Download, PackagePlus, ScanLine, ShieldCheck,
} from 'lucide-react';
import { z9ProfileExportUrl, getPapers, deleteZ9Profile } from '../../api/client';
import { geLabel } from '../../lib/geLabel.js';
import { saveFromUrl } from '../../lib/fileIO.js';
import ProfileCheckModal from './ProfileCheckModal.jsx';
import ConfirmModal from '../ui/ConfirmModal.jsx';

/**
 * Slide-over detail panel, 640px (17.2).
 *
 * Displays a profile's identity (mirror or repo) + different actions
 * depending on the zone:
 *  - mirror: Open in the inspector, Copy to the repo
 *  - repo  : Open in the inspector, Delete (with confirmation),
 *            Move/rename (inline form)
 */
export default function ProfileDetailPanel({
  selected, onClose, onOpenInspector, onValidate, onAction,
  allTags = [], onStoreRefresh,
}) {
  const { t } = useTranslation();
  const [checkOpen, setCheckOpen] = useState(false);   // "Check" modal (part a)

  // Esc to close
  useEffect(() => {
    if (!selected) return;
    const h = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [selected, onClose]);

  if (!selected) return null;
  const isMirror = selected.zone === 'mirror';
  const isZ9 = selected.zone === 'z9';
  // PRINTER profile of the generic personal repo (zone 'repo' + category 'printers').
  // Distinct from 'z9' (personal Z9 repo) and the mirror. Used to offer "Check" on
  // ALL printer profiles, NOT on displays/workspaces (repo + other categories).
  const isRepoPrinter = selected.zone === 'repo' && selected.category === 'printers';
  // 17.2.1 — B1: for the mirror, the source of truth for display is
  // `z9_icc_name` (the name exposed by the Z9),
  // not the filename. For the repo, it's display_name (user-entered) then
  // filename. For the personal Z9 space, it's the `label` entered when storing.
  const headerName = isMirror
    ? (selected.z9_icc_name || selected.filename)
    : isZ9
      ? (selected.label || selected.filename)
      : (selected.display_name || selected.filename);

  return (
    <>
    {/* Overlay = backdrop + FIXED panel on top (Measures/Papers pattern): no longer pushes
        the left content, right margin anchored to the viewport → filename not cut off. */}
    <div className="fixed inset-0 z-40 bg-black/10" onClick={onClose} aria-hidden="true"/>
    <aside
      role="dialog"
      aria-label={t('profils.detail_aria')}
      className="fixed top-12 right-0 bottom-0 z-50 w-[640px] bg-surface border-l border-border-soft
                 shadow-2xl overflow-y-auto flex flex-col animate-slidein">
      <div className="sticky top-0 bg-surface border-b border-border-soft
                      px-5 py-3 flex items-center gap-2 z-10">
        {isMirror && <Lock size={14} className="text-text-muted"/>}
        <h2 className="text-sm font-semibold text-text-strong truncate flex-1"
            title={headerName}>
          {headerName}
        </h2>
        <button type="button" onClick={onClose}
                className="w-7 h-7 rounded-md flex items-center justify-center
                           text-text-muted hover:text-text-strong hover:bg-sunken
                           transition-colors">
          <X size={14}/>
        </button>
      </div>

      <div className="px-5 py-4 space-y-4 flex-1">
        {/* Identity */}
        <Section title={t('profils.detail_identity')}>
          <Field label={t('profils.field_filename')} value={selected.filename} mono/>
          <Field label={t('profils.field_path')} value={selected.absolute_path} mono small/>
          {isMirror ? (
            <>
              <Field label={t('profils.field_paper')} value={selected.paperName}/>
              <Field label={t('profils.field_slot')}
                     value={`${geLabel(selected.gloss_enhancer)} · ${selected.color_space}`}/>
              <Field label={t('profils.field_type')}
                     value={selected.custom
                       ? t('profils.type_custom')
                       : t('profils.type_factory')}/>
              <Field label="UUID Z9" value={selected.z9_uuid} mono small/>
              {selected.z9_icc_name && (
                <Field label={t('profils.field_z9_name')} value={selected.z9_icc_name} small/>
              )}
              {selected.z9_date && (
                <Field label={t('profils.field_z9_date')} value={selected.z9_date}/>
              )}
            </>
          ) : isZ9 ? (
            <>
              <Field label={t('profils.field_paper')}
                     value={selected.paper_name || selected.paperName}/>
              <Field label={t('profils.field_label')} value={selected.label}/>
              <Field label={t('profils.field_slot')} value={geLabel(selected.gloss_slot)}/>
              {selected.method_flags && (
                <Field label={t('profils.field_method')}
                       value={selected.method_flags} mono small/>
              )}
              {selected.n_patches != null && (
                <Field label={t('profils.field_patches')} value={String(selected.n_patches)}/>
              )}
              {isZ9 ? (
                <TagsEditor key={selected.absolute_path} t={t} selected={selected}
                            allTags={allTags} onStoreRefresh={onStoreRefresh}/>
              ) : (Array.isArray(selected.purpose_tags) && selected.purpose_tags.length > 0 && (
                <Field label={t('profils.field_purpose')}
                       value={selected.purpose_tags.join(', ')}/>
              ))}
              {selected.notes && (
                <Field label={t('profils.field_notes')} value={selected.notes} small/>
              )}
              <Field label="MediaId" value={selected.media_id || selected.mediaId} mono small/>
            </>
          ) : (
            <>
              {selected.device && (
                <Field label={t('profils.field_device')} value={selected.device}/>
              )}
              <Field label={t('profils.field_category')} value={selected.category}/>
              {selected.origin && (
                <Field label={t('profils.field_origin')} value={selected.origin}/>
              )}
              {selected.origin_detail && (
                <Field label={t('profils.field_origin_detail')}
                       value={selected.origin_detail} mono small/>
              )}
            </>
          )}
          <Field label="MD5" value={selected.md5} mono small/>
          <Field label={t('profils.field_size')}
                 value={selected.size_bytes
                   ? `${selected.size_bytes.toLocaleString()} B`
                   : '—'} mono/>
          {(selected.fetched_at || selected.imported_at) && (
            <Field label={t('profils.field_when')}
                   value={selected.fetched_at || selected.imported_at} small/>
          )}
        </Section>

        {/* Actions — mirror/repo asymmetry (17.2.1 — C1).
            Mirror: strictly read-only. NO write action.
              → Open in the inspector + Copy to the repo. Full stop.
            Repo: full set (inspector + rename/move + delete).

            `key={selected.absolute_path}` (B2) guarantees the reset of the
            sub-components' local state on every selection change (otherwise
            MoveForm keeps the previous profile's values). */}
        <Section title={t('profils.detail_actions')}>
          <ActionButton
            icon={SearchIcon}
            label={t('profils.action_inspector')}
            onClick={() => onOpenInspector(selected.absolute_path)}/>

          {/* Check (self-consistency, part a) — PRINTER profiles only:
              mirror (= synced installed → checks the installed one without waking the Z9),
              personal Z9 repo, AND generic personal printers repo. NOT displays/workspaces
              (profcheck makes no sense for them; the zone separation already exists).
              The check auto-sources the embedded measurements (HP CIED / Argyll targ); a
              "dry" profile cleanly returns "no measurements". No Z9 action. */}
          {(isMirror || isZ9 || isRepoPrinter) && (
            <ActionButton
              icon={ShieldCheck}
              label={t('profils.action_check')}
              onClick={() => setCheckOpen(true)}/>
          )}
          {/* Validate (independent, profcheck b Mechanism 1) — machine action (print+scan).
              Reserved for profiles tied to a Z9 paper (mirror/repo z9): validation
              prints an independent chart ON THE PROFILE'S PAPER/GE. */}
          {(isMirror || isZ9) && onValidate && (
            <ActionButton
              icon={ScanLine}
              label={t('profils.action_validate')}
              onClick={() => onValidate(selected)}/>
          )}
          {isMirror ? (
            <CopyToRepoForm
              key={selected.absolute_path}
              selected={selected}
              onDone={onAction}/>
          ) : isZ9 ? (
            <Z9Actions
              key={selected.absolute_path}
              selected={selected}
              onDone={onAction}/>
          ) : (
            <RepoActions
              key={selected.absolute_path}
              selected={selected}
              onDone={onAction}/>
          )}
        </Section>

        {isMirror && (
          <div className="text-tiny text-text-faint italic px-1">
            {t('profils.mirror_readonly_hint')}
          </div>
        )}
      </div>

      <ProfileCheckModal open={checkOpen} target={selected} onClose={() => setCheckOpen(false)}/>
    </aside>
    </>
  );
}


function Section({ title, children }) {
  return (
    <div>
      <div className="text-tiny text-text-faint uppercase tracking-wider mb-2 px-1">
        {title}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}


function Field({ label, value, mono = false, small = false }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-2 px-1 text-xs2">
      <div className="text-text-faint">{label}</div>
      <div className={`${mono ? 'font-mono' : ''} ${small ? 'text-tiny' : ''}
                       text-text-strong break-all`}>
        {value || '—'}
      </div>
    </div>
  );
}


function ActionButton({ icon: Icon, label, onClick, danger = false, disabled = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`w-full inline-flex items-center gap-2 px-3 py-2 text-xs2
                  border rounded-md transition-colors text-left
                  disabled:opacity-50 disabled:cursor-not-allowed ${
                    danger
                      ? 'border-danger/40 text-danger hover:bg-danger/10'
                      : 'border-border-soft text-text-strong hover:bg-sunken/60 hover:border-border-strong'
                  }`}>
      <Icon size={13}/>
      <span>{label}</span>
    </button>
  );
}


function CopyToRepoForm({ selected, onDone }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(null);    // collision (409) → 3-choice pop-up
  // onConflict: null (collision → pop-up) | 'replace' | 'keep_both' (replay from the pop-up).
  const submit = async (onConflict = null) => {
    setBusy(true);
    try {
      // Copy to the dedicated Z9 repo (classified by paper). Installs NOTHING
      // on the printer (that happens on the Papers screen). serial/media_id/GE
      // are derived on the backend from the mirror path → we only send the source + the label.
      const body = {
        source_path: selected.absolute_path,
        label: selected.paperName + ' · ' + selected.gloss_enhancer
               + ' (' + (selected.custom ? 'custom' : 'factory') + ')',
      };
      if (onConflict) body.on_conflict = onConflict;   // replay after pop-up choice
      const r = await fetch('/api/profiles/mirror/copy-to-z9', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (r.status === 409 && data.detail?.error === 'name_conflict') {  // collision → pop-up
        setConflict(data.detail);
        return;
      }
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${r.status}`);
      await onDone?.();
    } catch (e) {
      alert(`${t('profils.copy_error')} ${e.message}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <ActionButton
        icon={Copy}
        label={busy ? t('profils.copy_in_progress') : t('profils.action_copy_to_repo')}
        onClick={() => submit()}
        disabled={busy}/>
      {/* Collision (409) → OS 3-choice pop-up (no name field here → direct). Replace overwrites
          the repo/z9 COPY (the source mirror survives); Keep both = -N. */}
      <ConfirmModal open={!!conflict}
        title={t('mesures.profile_conflict_title')}
        message={conflict ? t('mesures.profile_conflict_body', { name: conflict.name }) : ''}
        cancelLabel={t('common.cancel')}
        thirdLabel={t('mesures.profile_conflict_keep_both')}
        confirmLabel={t('mesures.profile_conflict_replace')} confirmKind="danger"
        busy={busy}
        onThird={() => { setConflict(null); submit('keep_both'); }}
        onConfirm={() => { setConflict(null); submit('replace'); }}
        onCancel={() => setConflict(null)}/>
    </>
  );
}


function RepoActions({ selected, onDone }) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Generic repo deletion (printers/screens/workspaces — never the mirror): .icc + .meta.
  // No dedicated client helper for /api/profiles/repo → fetch kept. Confirmation =
  // ConfirmModal (danger), unified. alert() = post-action error (out of scope, unchanged).
  const remove = async () => {
    const p = new URLSearchParams();
    p.set('category', selected.category);
    p.set('filename', selected.filename);
    if (selected.device) p.set('device', selected.device);
    setDeleting(true);
    try {
      const r = await fetch('/api/profiles/repo?' + p.toString(),
        { method: 'DELETE' });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      setConfirming(false);
      await onDone?.();
    } catch (e) {
      alert(`${t('profils.delete_error')} ${e.message}`);
    } finally {
      setDeleting(false);
    }
  };
  return (
    <>
      <MoveForm selected={selected} onDone={onDone}/>
      <ActionButton
        icon={Trash2}
        label={t('profils.action_delete')}
        danger
        onClick={() => setConfirming(true)}/>
      <ConfirmModal open={confirming}
        title={t('profils.delete_confirm', { name: selected.display_name || selected.filename })}
        message={t('profils.delete_irreversible')}
        confirmLabel={t('profils.delete_confirm_yes')} confirmKind="danger"
        cancelLabel={t('profils.delete_confirm_no')}
        busy={deleting}
        onConfirm={remove}
        onCancel={() => setConfirming(false)}/>
    </>
  );
}


function Z9Actions({ selected, onDone }) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [installMsg, setInstallMsg] = useState(null);
  const installToPaper = async () => {
    const mediaId = selected.media_id || selected.mediaId;
    const gloss = String(selected.gloss_slot || '').toUpperCase();
    setInstallMsg(null);
    let list;
    try {
      list = (await getPapers()).papers || [];
    } catch (e) {
      setInstallMsg(t('profils.install_check_error', { message: e.message }));
      return;
    }
    const paper = list.find((p) => p.mediaid === mediaId);
    if (!paper) { setInstallMsg(t('profils.install_paper_absent')); return; }
    if (paper.factory) { setInstallMsg(t('profils.install_paper_factory')); return; }
    const desired = (gloss === 'FULLPAGE' || gloss === 'ON') ? 'on' : 'off';
    const kinds = (paper.icc || []).map((s) => s.kind);
    const ge = kinds.includes(desired === 'on' ? 'ge_on' : 'ge_off') ? desired
      : kinds.includes('single') ? 'single' : desired;
    window.history.pushState(null, '', `/papers#paper=${mediaId}&install=${ge}`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };
  // Repo/z9 deletion (.icc + .meta tags/notes together, never the mirror). Client helper
  // deleteZ9Profile (replaces the raw fetch, 1:1). Confirmation = ConfirmModal (danger), unified
  // with chart/scan/lighten. alert() = post-action error (out of scope, unchanged).
  const remove = async () => {
    setDeleting(true);
    try {
      await deleteZ9Profile({
        serial: selected.serial,
        mediaId: selected.media_id || selected.mediaId,
        filename: selected.filename,
      });
      setConfirming(false);
      await onDone?.();
    } catch (e) {
      alert(`${t('profils.delete_error')} ${e.message}`);
    } finally {
      setDeleting(false);
    }
  };
  const exportProfile = async () => {
    // Fetch then save (desktop-aware) — never navigate the webview to the export
    // URL (freezes the desktop app, #22).
    const url = z9ProfileExportUrl({
      serial: selected.serial,
      mediaId: selected.media_id || selected.mediaId,
      filename: selected.filename,
    });
    try {
      await saveFromUrl(url, selected.filename || 'profil.icc', 'application/vnd.iccprofile');
    } catch (e) {
      alert(t('profils.export_error', { message: e.message }));
    }
  };
  return (
    <>
      <ActionButton
        icon={PackagePlus}
        label={t('profils.action_install')}
        onClick={installToPaper}/>
      {installMsg && (
        <div className="p-2 border border-icc-warn/40 rounded-md bg-icc-warn/5 text-xs2 text-text-strong leading-snug">
          {installMsg}
        </div>
      )}
      <ActionButton
        icon={Download}
        label={t('profils.action_export')}
        onClick={exportProfile}/>
      <Z9RenameForm selected={selected} onDone={onDone}/>
      <ActionButton
        icon={Trash2}
        label={t('profils.action_delete')}
        danger
        onClick={() => setConfirming(true)}/>
      <ConfirmModal open={confirming}
        title={t('profils.delete_confirm', { name: selected.label || selected.filename })}
        message={t('profils.delete_irreversible')}
        confirmLabel={t('profils.delete_confirm_yes')} confirmKind="danger"
        cancelLabel={t('profils.delete_confirm_no')}
        busy={deleting}
        onConfirm={remove}
        onCancel={() => setConfirming(false)}/>
    </>
  );
}


// Classification tags editor (purpose_tags) — chips + add with autocomplete.
// Persistence: POST /api/profiles/z9/tags (inline pattern from rename) → .meta ONLY,
// the .icc is NEVER touched. Strict ASCII on input (immediate feedback).
const _isAscii = (s) => !/[^\x00-\x7F]/.test(s);

function TagsEditor({ t, selected, allTags, onStoreRefresh }) {
  const [tags, setTags] = useState(() => (Array.isArray(selected.purpose_tags) ? selected.purpose_tags : []));
  const [input, setInput] = useState('');
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  const persist = async (next) => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch('/api/profiles/z9/tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          serial: selected.serial,
          media_id: selected.media_id || selected.mediaId,
          filename: selected.filename,
          tags: next,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      setTags(data.purpose_tags || next);
      onStoreRefresh?.();                       // reloads the store (list + filter), without closing
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const addTag = () => {
    const v = input.trim();
    if (!v) return;
    if (!_isAscii(v)) { setErr(t('profils.tags_ascii_only')); return; }
    if (tags.includes(v)) { setInput(''); return; }
    persist([...tags, v]); setInput('');
  };
  const removeTag = (tg) => persist(tags.filter((x) => x !== tg));

  const q = input.trim().toLowerCase();
  const suggestions = q
    ? (allTags || []).filter((tg) => !tags.includes(tg) && tg.toLowerCase().includes(q)).slice(0, 6)
    : [];

  return (
    <div className="space-y-1.5">
      <div className="text-tiny text-text-faint uppercase tracking-wider">{t('profils.tags_label')}</div>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((tg) => (
          <span key={tg} className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full
                                    bg-sunken text-xs2 text-text-strong border border-border-soft">
            {tg}
            <button type="button" onClick={() => removeTag(tg)} disabled={busy}
                    aria-label={t('profils.tags_remove', { tag: tg })}
                    className="w-4 h-4 rounded-full flex items-center justify-center
                               text-text-faint hover:text-danger hover:bg-danger/10 disabled:opacity-40">
              <X size={11}/>
            </button>
          </span>
        ))}
        {tags.length === 0 && <span className="text-tiny text-text-faint italic">{t('profils.tags_empty')}</span>}
      </div>
      <div className="relative">
        <input type="text" value={input} disabled={busy}
               onChange={(e) => { setInput(e.target.value); setErr(null); }}
               onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }}
               placeholder={t('profils.tags_add_placeholder')}
               className="w-full px-2 py-1 text-xs2 bg-bg border border-border-soft rounded
                          focus:outline-none focus:border-accent"/>
        {suggestions.length > 0 && (
          <div className="absolute z-10 left-0 right-0 mt-0.5 bg-surface border border-border-soft
                          rounded-md shadow-lg overflow-hidden">
            {suggestions.map((tg) => (
              <button type="button" key={tg} onMouseDown={(e) => e.preventDefault()}
                      onClick={() => { if (!tags.includes(tg)) persist([...tags, tg]); setInput(''); }}
                      className="w-full text-left px-2 py-1 text-xs2 text-text-strong hover:bg-sunken">
                {tg}
              </button>
            ))}
          </div>
        )}
      </div>
      {err && <div className="text-tiny text-danger">{err}</div>}
    </div>
  );
}


function Z9RenameForm({ selected, onDone }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(selected.label || selected.filename);
  const submit = async () => {
    try {
      const r = await fetch('/api/profiles/z9/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          serial: selected.serial,
          media_id: selected.media_id || selected.mediaId,
          filename: selected.filename,
          new_label: name.trim(),
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      await onDone?.();
    } catch (e) {
      alert(`${t('profils.move_error')} ${e.message}`);
    }
  };
  if (!open) {
    return (
      <ActionButton
        icon={FolderInput}
        label={t('profils.action_rename')}
        onClick={() => setOpen(true)}/>
    );
  }
  return (
    <div className="p-2 border border-border-soft rounded-md bg-sunken/30 space-y-2">
      <div className="text-tiny text-text-faint uppercase tracking-wider">
        {t('profils.rename_title')}
      </div>
      <input type="text" value={name} onChange={(e) => setName(e.target.value)}
             placeholder={t('profils.rename_placeholder')}
             className="w-full px-2 py-1 text-xs2 bg-bg border border-border-soft
                        rounded focus:outline-none focus:border-accent"/>
      <div className="flex gap-2 pt-1">
        <button type="button" onClick={submit} disabled={!name.trim()}
                className="flex-1 px-3 py-1.5 text-xs2 bg-accent/10 text-accent
                           rounded hover:bg-accent/15 transition-colors font-medium
                           disabled:opacity-50 disabled:cursor-not-allowed">
          {t('profils.rename_apply')}
        </button>
        <button type="button" onClick={() => setOpen(false)}
                className="px-3 py-1.5 text-xs2 border border-border-soft
                           rounded text-text-muted hover:text-text-strong
                           hover:bg-sunken transition-colors">
          {t('common.cancel')}
        </button>
      </div>
    </div>
  );
}


function MoveForm({ selected, onDone }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [cat, setCat] = useState(selected.category);
  const [device, setDevice] = useState(selected.device || '');
  const [name, setName] = useState(selected.display_name || selected.filename);
  const submit = async () => {
    const body = {
      category: selected.category,
      filename: selected.filename,
      device: selected.device,
    };
    if (cat !== selected.category) body.new_category = cat;
    if (cat === 'printers' && device && device !== selected.device) {
      body.new_device = device;
    }
    if (name && name !== (selected.display_name || selected.filename)) {
      body.new_display_name = name;
    }
    try {
      const r = await fetch('/api/profiles/repo', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      await onDone?.();
    } catch (e) {
      alert(`${t('profils.move_error')} ${e.message}`);
    }
  };
  if (!open) {
    return (
      <ActionButton
        icon={FolderInput}
        label={t('profils.action_move_rename')}
        onClick={() => setOpen(true)}/>
    );
  }
  return (
    <div className="p-2 border border-border-soft rounded-md bg-sunken/30 space-y-2">
      <div className="text-tiny text-text-faint uppercase tracking-wider">
        {t('profils.action_move_rename')}
      </div>
      <label className="block">
        <span className="block text-tiny text-text-muted mb-0.5">
          {t('profils.field_category')}
        </span>
        <select value={cat} onChange={(e) => setCat(e.target.value)}
                className="w-full px-2 py-1 text-xs2 bg-bg border border-border-soft
                           rounded focus:outline-none focus:border-accent">
          <option value="printers">printers</option>
          <option value="displays">displays</option>
          <option value="workingspaces">workingspaces</option>
        </select>
      </label>
      {cat === 'printers' && (
        <label className="block">
          <span className="block text-tiny text-text-muted mb-0.5">
            {t('profils.field_device')}
          </span>
          <input type="text" value={device} onChange={(e) => setDevice(e.target.value)}
                 placeholder="Z9"
                 className="w-full px-2 py-1 text-xs2 bg-bg border border-border-soft
                            rounded focus:outline-none focus:border-accent"/>
        </label>
      )}
      <label className="block">
        <span className="block text-tiny text-text-muted mb-0.5">
          {t('profils.field_display_name')}
        </span>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)}
               className="w-full px-2 py-1 text-xs2 bg-bg border border-border-soft
                          rounded focus:outline-none focus:border-accent"/>
      </label>
      <div className="flex gap-2 pt-1">
        <button type="button" onClick={submit}
                className="flex-1 px-3 py-1.5 text-xs2 bg-accent/10 text-accent
                           rounded hover:bg-accent/15 transition-colors font-medium">
          {t('profils.move_apply')}
        </button>
        <button type="button" onClick={() => setOpen(false)}
                className="px-3 py-1.5 text-xs2 border border-border-soft
                           rounded text-text-muted hover:text-text-strong
                           hover:bg-sunken transition-colors">
          {t('common.cancel')}
        </button>
      </div>
    </div>
  );
}


