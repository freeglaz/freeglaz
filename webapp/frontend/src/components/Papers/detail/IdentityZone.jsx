import { useState } from 'react';
import { Clipboard, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Zone 1 — Identity (spec §5 Zone 1). Read-only in P1.
 *
 * KV pairs: Name, Category, Donor (with faint mono id), MEDIAID
 * (mono + copy button), Inks, Capabilities (4 badges),
 * Advanced mechanical properties (collapsed header, empty in P1).
 */
export default function IdentityZone({ paper }) {
  const { t } = useTranslation();
  const inks = _inksLabel(paper.inks, t);
  return (
    <section className="px-5 py-4 border-b border-border-soft">
      <h3 className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted mb-3">
        {t('papers.detail.section_identity')}
      </h3>
      <dl className="space-y-2">
        <Row label={t('papers.detail.field_name')} value={paper.name}/>
        <Row label={t('papers.detail.field_category')} value={paper.category}/>
        {paper.donor_name && (
          <Row label={t('papers.detail.field_donor')}>
            <span className="text-[12.5px] text-text-strong">{paper.donor_name}</span>
            {paper.donor_id && (
              <span className="ml-1.5 font-mono text-tiny text-text-faint">
                ({paper.donor_id})
              </span>
            )}
          </Row>
        )}
        <Row label={t('papers.detail.field_mediaid')}>
          <CopyableMediaid mediaid={paper.mediaid}/>
        </Row>
        <Row label={t('papers.detail.field_inks')} value={inks}/>
      </dl>

      {/* Capabilities as badges — "absent if not supported" rule.
          No greyed-out badge for the unsupported ones. */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {paper.capabilities?.borderless && (
          <CapBadge label={t('papers.detail.cap_borderless')} title={t('papers.detail.cap_borderless_tooltip')}/>
        )}
        {paper.capabilities?.ge && (
          <CapBadge label={t('papers.detail.cap_ge')} title={t('papers.detail.cap_ge_tooltip')}/>
        )}
        {paper.capabilities?.max_detail && (
          <CapBadge label={t('papers.detail.cap_max_detail')} title={t('papers.detail.cap_max_detail_tooltip')}/>
        )}
        {paper.capabilities?.scanning && (
          <CapBadge label={t('papers.detail.cap_scan')} title={t('papers.detail.cap_scan_tooltip')}/>
        )}
        {!_anyCapability(paper.capabilities) && (
          <span className="text-tiny text-text-faint italic">
            {t('papers.detail.cap_none')}
          </span>
        )}
      </div>

    </section>
  );
}


function Row({ label, value, children }) {
  return (
    <div className="flex items-start">
      <dt className="text-xs text-text-muted w-[140px] flex-shrink-0 pt-0.5">{label}</dt>
      <dd className="flex-1 text-[12.5px] text-text-strong min-w-0">
        {children || value || <span className="text-text-faint italic">—</span>}
      </dd>
    </div>
  );
}


function CopyableMediaid({ mediaid }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(mediaid);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* silent */ }
  };
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-[12.5px] text-text-strong break-all select-all">
        {mediaid}
      </span>
      <button
        type="button"
        onClick={handleCopy}
        aria-label={t('papers.detail.copy_mediaid_label')}
        title={copied ? t('papers.detail.copy_mediaid_done') : t('papers.detail.copy_mediaid_label')}
        className="flex-shrink-0 p-1 rounded text-text-muted hover:text-accent hover:bg-sunken transition-colors">
        {copied ? <Check size={11} aria-hidden="true"/> : <Clipboard size={11} aria-hidden="true"/>}
      </button>
    </div>
  );
}


function CapBadge({ label, title }) {
  return (
    <span
      title={title || label}
      className="flex items-center gap-1 px-2 py-0.5 rounded text-tiny font-medium border bg-accent/10 border-accent/30 text-accent">
      <span aria-hidden="true">✓</span>
      {label}
    </span>
  );
}


function _anyCapability(caps) {
  if (!caps) return false;
  return Boolean(
    caps.borderless || caps.ge || caps.max_detail || caps.scanning
  );
}


function _inksLabel(inks, t) {
  if (!inks) return t('papers.detail.ink_none');
  const { pk, mk } = inks;
  if (pk && mk) return t('papers.detail.ink_pk_mk');
  if (pk) return t('papers.detail.ink_pk_only');
  if (mk) return t('papers.detail.ink_mk_only');
  return t('papers.detail.ink_none');
}
