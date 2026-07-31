import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Printer, Plus, Pencil, Trash2, CheckCircle2, AlertTriangle, Loader2, Check, MonitorPlay } from 'lucide-react';
import * as api from '../../api/client.js';
import Modal from '../wizards/shared/Modal.jsx';
import ModalHeader from '../wizards/shared/ModalHeader.jsx';
import ConfirmModal from '../ui/ConfirmModal.jsx';

/**
 * Printers modal (IP setup): list + add flow (IP → Test → Save). NON-blocking
 * reboot banner when the active printer changes (the Z9 client is instantiated
 * once at startup — no hot repointing).
 */
export default function PrintersModal({ open, onClose }) {
  const { t } = useTranslation();
  // PrintersPanel is mounted ONLY when open (Modal returns null otherwise) → its
  // state (reboot, list) starts fresh on each opening, with no reset effect.
  return (
    <Modal open={open} onClose={onClose} ariaLabel={t('settings.printers.title')} widthClass="w-[620px]">
      <PrintersPanel onClose={onClose}/>
    </Modal>
  );
}

function PrintersPanel({ onClose }) {
  const { t } = useTranslation();

  const [printers, setPrinters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reboot, setReboot] = useState(false);
  const [confirmDel, setConfirmDel] = useState(null);   // serial to delete
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.getPrinters();
      setPrinters(r.printers || []);
    } catch {
      setPrinters([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Deferred initial fetch (microtask) → no synchronous setState in the effect.
  useEffect(() => {
    let alive = true;
    Promise.resolve().then(() => { if (alive) refresh(); });
    return () => { alive = false; };
  }, [refresh]);

  const onActivate = async (serial) => {
    setBusy(true);
    try { await api.activatePrinter(serial); await refresh(); setReboot(true); }
    finally { setBusy(false); }
  };

  const onDelete = async () => {
    const serial = confirmDel;
    setBusy(true);
    try {
      const wasActive = printers.find((p) => p.serial === serial)?.active;
      await api.deletePrinter(serial);
      setConfirmDel(null);
      await refresh();
      if (wasActive) setReboot(true);
    } finally { setBusy(false); }
  };

  return (
    <>
      <ModalHeader
        eyebrow={t('settings.printers.eyebrow')}
        title={t('settings.printers.title')}
        subtitle={t('settings.printers.subtitle')}
        onClose={onClose}/>

      {reboot && (
        <div className="mx-[22px] mb-3 flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5">
          <AlertTriangle size={15} className="text-warn mt-0.5 flex-shrink-0"/>
          <p className="text-xs text-text-strong leading-relaxed">{t('settings.printers.reboot_banner')}</p>
        </div>
      )}

      <div className="px-[22px] pb-[20px] overflow-y-auto">
        {/* ── List ── */}
        {loading ? (
          <div className="flex items-center gap-2 text-text-muted text-sm py-6 justify-center">
            <Loader2 size={15} className="animate-spin"/> {t('common.loading')}
          </div>
        ) : printers.length === 0 ? (
          <div className="text-center py-8 text-text-muted text-sm">
            <Printer size={28} className="mx-auto mb-2 opacity-40" strokeWidth={1.5}/>
            {t('settings.printers.empty')}
          </div>
        ) : (
          <ul className="space-y-2 mb-5">
            {printers.map((p) => (
              <PrinterRow
                key={p.serial} p={p} busy={busy}
                onActivate={() => onActivate(p.serial)}
                onEdit={(body) => api.updatePrinter(p.serial, body).then(refresh)}
                onDelete={() => setConfirmDel(p.serial)}/>
            ))}
          </ul>
        )}

        {/* ── Add ── */}
        <AddPrinter
          hasPrinters={printers.length > 0}
          onAdded={() => { refresh(); }}
          onFirstAdded={() => setReboot(true)}/>
      </div>

      <ConfirmModal
        open={confirmDel !== null}
        title={t('settings.printers.delete_confirm_title')}
        message={t('settings.printers.delete_confirm_message')}
        icon={<Trash2 size={18} className="text-danger"/>}
        confirmKind="danger"
        confirmLabel={t('settings.printers.delete')}
        cancelLabel={t('common.cancel')}
        busy={busy}
        onConfirm={onDelete}
        onCancel={() => setConfirmDel(null)}/>
    </>
  );
}

// ─── A printer row (display + inline IP/name editing) ──────────
function PrinterRow({ p, busy, onActivate, onEdit, onDelete }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [ip, setIp] = useState(p.ip);
  const [name, setName] = useState(p.name || '');
  const [adminPwd, setAdminPwd] = useState('');   // blank = leave unchanged
  const [clearPwd, setClearPwd] = useState(false);
  const [saving, setSaving] = useState(false);

  const cancelEdit = () => {
    setEditing(false); setIp(p.ip); setName(p.name || '');
    setAdminPwd(''); setClearPwd(false);
  };

  const save = async () => {
    setSaving(true);
    // admin_pwd: "" = clear, a value = set, omitted = leave unchanged
    const body = { ip: ip.trim(), name: name.trim() };
    if (clearPwd) body.admin_pwd = '';
    else if (adminPwd.trim()) body.admin_pwd = adminPwd.trim();
    try { await onEdit(body); cancelEdit(); }
    finally { setSaving(false); }
  };

  return (
    <li className="rounded-lg border border-border-soft bg-surface px-3.5 py-3">
      <div className="flex items-center gap-3">
        <Printer size={16} className={p.active ? 'text-accent' : 'text-text-faint'} strokeWidth={1.7}/>
        <div className="flex-1 min-w-0">
          {editing ? (
            <input
              className="w-full bg-sunken/50 border border-border-soft rounded px-2 py-1 text-sm text-text-strong"
              value={name} onChange={(e) => setName(e.target.value)}
              placeholder={t('settings.printers.name_placeholder')}/>
          ) : (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-text-strong truncate">{p.name || p.serial}</span>
              {p.active && (
                <span className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-accent/15 text-accent">
                  {t('settings.printers.badge_active')}
                </span>
              )}
              {p.model_support === 'untested' && (
                <span className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-warn/15 text-warn">
                  {t('settings.printers.badge_untested')}
                </span>
              )}
            </div>
          )}
          {editing ? (
            <input
              className="w-full mt-1.5 bg-sunken/50 border border-border-soft rounded px-2 py-1 text-xs font-mono text-text-muted"
              value={ip} onChange={(e) => setIp(e.target.value)}/>
          ) : (
            <div className="text-xs text-text-muted font-mono mt-0.5">{p.ip} · {p.serial}</div>
          )}
          {editing && (
            <div className="mt-1.5">
              <input
                type="password" autoComplete="off" disabled={clearPwd}
                className="w-full bg-sunken/50 border border-border-soft rounded px-2 py-1 text-xs text-text-strong disabled:opacity-40"
                placeholder={p.has_admin_pwd ? t('settings.printers.admin_pwd_set')
                                             : t('settings.printers.admin_pwd_placeholder')}
                value={adminPwd} onChange={(e) => setAdminPwd(e.target.value)}/>
              {p.has_admin_pwd && (
                <label className="mt-1 flex items-center gap-1.5 text-[11px] text-text-muted cursor-pointer">
                  <input type="checkbox" checked={clearPwd}
                         onChange={(e) => { setClearPwd(e.target.checked); if (e.target.checked) setAdminPwd(''); }}/>
                  {t('settings.printers.admin_pwd_remove')}
                </label>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-1 flex-shrink-0">
          {editing ? (
            <>
              <button className="btn-secondary text-xs px-2.5 py-1.5" disabled={saving} onClick={save}>
                {saving ? <Loader2 size={13} className="animate-spin"/> : t('common.save')}
              </button>
              <button className="btn-ghost text-xs px-2 py-1.5" disabled={saving}
                      onClick={cancelEdit}>
                {t('common.cancel')}
              </button>
            </>
          ) : (
            <>
              {!p.active && (
                <button title={t('settings.printers.set_active')} disabled={busy}
                        className="btn-ghost px-2 py-1.5" onClick={onActivate}>
                  <CheckCircle2 size={15}/>
                </button>
              )}
              <button title={t('settings.printers.edit')} disabled={busy}
                      className="btn-ghost px-2 py-1.5" onClick={() => setEditing(true)}>
                <Pencil size={14}/>
              </button>
              <button title={t('settings.printers.delete')} disabled={busy}
                      className="btn-ghost px-2 py-1.5 text-danger hover:text-danger" onClick={onDelete}>
                <Trash2 size={14}/>
              </button>
            </>
          )}
        </div>
      </div>
    </li>
  );
}

// ─── ADD flow (single block, progressive reveal) ────────────────────
function AddPrinter({ hasPrinters, onAdded, onFirstAdded }) {
  const { t } = useTranslation();
  const [reveal, setReveal] = useState(false);
  const [ip, setIp] = useState('');
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);   // {status, model, serial, …}
  const [name, setName] = useState('');
  const [adminPwd, setAdminPwd] = useState('');
  const [saving, setSaving] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);

  // Offline demo: swap in the mock Z9 (hot, no restart), then reload. Enabling
  // demo restarts the backend subscribers, but the EXISTING status SSE stays
  // bound to the old one → a reload gets a fresh stream that reflects demo mode.
  const tryDemo = async () => {
    setDemoLoading(true);
    try {
      await api.enableDemo();
      window.location.reload();
    } catch {
      setDemoLoading(false);
    }
  };

  const reset = () => { setIp(''); setResult(null); setName(''); setAdminPwd(''); setReveal(false); };

  const runTest = async () => {
    setTesting(true); setResult(null);
    try {
      const r = await api.testPrinter(ip.trim());
      setResult(r);
      if (r.status === 'ok') setName(r.model || r.serial || '');
    } catch (e) {
      setResult({ status: 'unreachable', message: e.message });
    } finally { setTesting(false); }
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.addPrinter({ ip: ip.trim(), serial: result.serial, name: name.trim(),
                             model_support: result.model_support,
                             admin_pwd: adminPwd.trim() || undefined });
      const wasFirst = !hasPrinters;
      reset();
      onAdded();
      if (wasFirst) onFirstAdded();
    } finally { setSaving(false); }
  };

  if (!reveal) {
    return (
      <div className="flex items-center gap-3 flex-wrap">
        <button className="btn-secondary text-sm px-3.5 py-2 inline-flex items-center gap-1.5"
                onClick={() => setReveal(true)}>
          <Plus size={15}/> {t('settings.printers.add_button')}
        </button>
        <span className="text-xs text-text-muted">{t('settings.printers.demo_or')}</span>
        <button className="btn-secondary text-sm px-3.5 py-2 inline-flex items-center gap-1.5"
                disabled={demoLoading} onClick={tryDemo}
                title={t('settings.printers.demo_hint')}>
          {demoLoading ? <Loader2 size={15} className="animate-spin"/> : <MonitorPlay size={15}/>}
          {t('settings.printers.demo_button')}
        </button>
      </div>
    );
  }

  const ok = result?.status === 'ok';
  return (
    <div className="rounded-lg border border-border-soft bg-sunken/30 p-3.5 space-y-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-accent font-mono">
        {t('settings.printers.add_title')}
      </div>

      <div className="flex gap-2 items-end">
        <div className="flex-1">
          <label className="block text-xs text-text-muted mb-1">{t('settings.printers.add_ip_label')}</label>
          <input
            className="w-full bg-surface border border-border-soft rounded-md px-2.5 py-2 text-sm font-mono text-text-strong"
            placeholder={t('settings.printers.add_ip_placeholder')}
            value={ip} onChange={(e) => { setIp(e.target.value); setResult(null); }}
            onKeyDown={(e) => { if (e.key === 'Enter' && ip.trim() && !testing) runTest(); }}/>
        </div>
        <button className="btn-secondary text-sm px-3.5 py-2" disabled={!ip.trim() || testing} onClick={runTest}>
          {testing ? <Loader2 size={14} className="animate-spin"/> : t('settings.printers.test_button')}
        </button>
      </div>

      {/* Test result */}
      {result && result.status === 'unreachable' && (
        <Notice kind="error" text={t('settings.printers.result_unreachable')}/>
      )}
      {result && result.status === 'not_a_designjet' && (
        <Notice kind="error" text={t('settings.printers.result_not_designjet')}/>
      )}
      {ok && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 rounded-md border border-ok/40 bg-ok/10 px-3 py-2">
            <Check size={15} className="text-ok mt-0.5 flex-shrink-0"/>
            <div className="text-xs text-text-strong leading-relaxed">
              <div className="font-semibold">{result.model}</div>
              <div className="text-text-muted font-mono mt-0.5">
                {t('settings.printers.serial')}: {result.serial}
                {result.firmware ? ` · ${result.firmware}` : ''}
              </div>
            </div>
          </div>
          {result.model_support === 'untested' && (
            <Notice kind="warn" text={t('settings.printers.untested_warning')}/>
          )}
          <div>
            <label className="block text-xs text-text-muted mb-1">{t('settings.printers.name_label')}</label>
            <input
              className="w-full bg-surface border border-border-soft rounded-md px-2.5 py-2 text-sm text-text-strong"
              placeholder={t('settings.printers.name_placeholder')}
              value={name} onChange={(e) => setName(e.target.value)}/>
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1">{t('settings.printers.admin_pwd_label')}</label>
            <input
              type="password" autoComplete="off"
              className="w-full bg-surface border border-border-soft rounded-md px-2.5 py-2 text-sm text-text-strong"
              placeholder={t('settings.printers.admin_pwd_placeholder')}
              value={adminPwd} onChange={(e) => setAdminPwd(e.target.value)}/>
            <p className="text-[11px] text-text-faint mt-1 leading-relaxed">{t('settings.printers.admin_pwd_hint')}</p>
          </div>
        </div>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <button className="btn-ghost text-sm px-3 py-2" onClick={reset}>{t('common.cancel')}</button>
        <button className="btn-primary text-sm px-3.5 py-2" disabled={!ok || saving} onClick={save}>
          {saving ? <Loader2 size={14} className="animate-spin"/> : t('settings.printers.save')}
        </button>
      </div>
    </div>
  );
}

function Notice({ kind, text }) {
  const cls = kind === 'warn'
    ? 'border-warn/40 bg-warn/10 text-text-strong'
    : 'border-danger/40 bg-danger/10 text-text-strong';
  const Icon = kind === 'warn' ? AlertTriangle : AlertTriangle;
  const iconCls = kind === 'warn' ? 'text-warn' : 'text-danger';
  return (
    <div className={`flex items-start gap-2 rounded-md border px-3 py-2 ${cls}`}>
      <Icon size={14} className={`${iconCls} mt-0.5 flex-shrink-0`}/>
      <p className="text-xs leading-relaxed">{text}</p>
    </div>
  );
}
