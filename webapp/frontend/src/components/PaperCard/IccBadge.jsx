import Badge from '../ui/Badge.jsx';
import { Check, XCircle, HelpCircle, Minus } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// Functional ICC badge. Compares the file's ICC profile vs that of the
// paper loaded on the Z9. 4 visual states, each with a distinctly shaped
// Lucide icon (red-green color-blindness accessibility):
//
//   'match'    → green Check       ✓   byte-identical profiles (MD5) or name (fallback)
//   'mismatch' → red XCircle       ✗   confirmed different profiles — distorted rendering
//   'unknown'  → amber HelpCircle  ❓  comparison impossible (paper not resolved)
//   'none'     → gray Minus        —   file without embedded ICC profile
//
// The 'none' vs 'unknown' distinction is deliberate and aligned with freeglaz's
// core mission (cf. Docs/freeglaz_Webapp_Roadmap.md §1): make the invisible
// VISIBLE. 'none' is NOT a silent fallback to sRGB — it is deterministic
// info that must be signaled explicitly.
export default function IccBadge({
  status = 'none',
  fileProfile,
  paperProfile,
  reason,
}) {
  const { t } = useTranslation();
  const tooltip = _tooltipText({ t, status, fileProfile, paperProfile, reason });

  const content =
    status === 'match'    ? <><span>ICC</span><Check       size={10} strokeWidth={3}  /></> :
    status === 'mismatch' ? <><span>ICC</span><XCircle    size={10} strokeWidth={2.5}/></> :
    status === 'unknown'  ? <><span>ICC</span><HelpCircle size={10} strokeWidth={2.5}/></> :
                            <><span>ICC</span><Minus      size={10} strokeWidth={3}  /></>;

  const kind =
    status === 'match'    ? 'ok'      :
    status === 'mismatch' ? 'danger'  :
    status === 'unknown'  ? 'warn'    :
                            'neutral';

  return (
    <span className="relative group">
      <Badge kind={kind}>{content}</Badge>
      <span className="pointer-events-none absolute z-20 top-full left-0 mt-1.5 hidden group-hover:block
                       w-[300px] p-2.5 bg-surface border border-border-soft rounded-md shadow-lg
                       text-xs2 text-text-strong leading-relaxed whitespace-normal">
        {tooltip}
      </span>
    </span>
  );
}


function _tooltipText({ t, status, fileProfile, paperProfile, reason }) {
  if (status === 'match') {
    return t('print.icc_tooltip_match', { paper: paperProfile || '—' });
  }
  if (status === 'mismatch') {
    return (
      t('print.icc_tooltip_mismatch_head') + '\n'
      + t('print.icc_tooltip_mismatch_file',  { name: fileProfile  || '—' }) + '\n'
      + t('print.icc_tooltip_mismatch_paper', { name: paperProfile || '—' }) + '\n'
      + (reason ? t('print.icc_tooltip_mismatch_reason', { reason }) + '\n' : '')
      + t('print.icc_tooltip_mismatch_advice')
    );
  }
  if (status === 'unknown') {
    return (
      t('print.icc_tooltip_unknown_head') + '\n'
      + (reason || t('print.icc_tooltip_unknown_default'))
    );
  }
  // none
  return (
    t('print.icc_tooltip_none_head') + '\n'
    + t('print.icc_tooltip_none_body') + '\n'
    + t('print.icc_tooltip_none_advice')
  );
}
