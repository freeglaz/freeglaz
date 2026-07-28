import { FileText, Disc, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import Badge from '../ui/Badge.jsx';
import IccBadge from './IccBadge.jsx';
import { fmtMm1, fmtDims1 } from '../../lib/format.js';

/**
 * @param {{
 *   paper: import('../../api/types').Paper | null,
 *   iccStatus?: 'none'|'match'|'mismatch'|'unknown',
 *   fileProfile?: string|null,
 *   iccReason?: string|null,
 * }} props
 */
export default function PaperCard({
  paper,
  iccStatus = 'none',
  fileProfile,
  iccReason,
}) {
  const { t } = useTranslation();
  // State D: no paper loaded in the Z9.
  if (!paper) {
    return (
      <div className="bg-danger/10 border border-danger/30 rounded-[10px] px-4 py-3.5 flex items-start gap-3">
        <AlertTriangle size={16} className="text-danger mt-0.5 flex-shrink-0"/>
        <div>
          <div className="text-[13px] font-semibold text-text-strong mb-0.5">{t('print.paper_card_no_paper_detected')}</div>
          <div className="text-xs2 text-text-muted leading-snug">
            {t('print.paper_card_no_paper_hint_long')}
          </div>
        </div>
      </div>
    );
  }

  const isRoll = paper.kind === 'roll';
  return (
    <div className="card px-4 py-3.5">
      <div className="flex items-center gap-3 mb-2.5">
        <div className="w-[34px] h-[34px] rounded-md bg-sunken flex items-center justify-center text-text-muted">
          {isRoll ? <Disc size={18}/> : <FileText size={18}/>}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold text-text-strong tracking-tight whitespace-nowrap overflow-hidden text-ellipsis">
            {paper.name}
          </div>
          <div className="text-xs2 text-text-muted mt-0.5 font-mono tabular-nums">
            {isRoll
              ? t('print.paper_card_roll', { width: fmtMm1(paper.width_mm) })
              : t('print.paper_card_sheet', { dims: fmtDims1(paper.width_mm, paper.height_mm) })}
          </div>
        </div>
      </div>

      <div className="flex gap-1.5 flex-wrap items-center">
        {paper.capabilities.includes('gloss_enhancer') && <Badge>{t('print.paper_card_gloss_enhancer')}</Badge>}
        {paper.capabilities.includes('max_detail') && <Badge>{t('print.paper_card_max_detail')}</Badge>}
        <IccBadge
          status={iccStatus}
          fileProfile={fileProfile}
          paperProfile={paper.icc_profile}
          reason={iccReason}/>
      </div>

      {iccStatus === 'match' && (
        <div className="text-micro text-text-faint mt-2 font-mono leading-snug">
          {t('print.paper_card_profile_label', { name: paper.icc_profile || '—' })}
        </div>
      )}
      {iccStatus === 'mismatch' && (
        <div className="text-micro text-danger mt-2 font-mono leading-snug">
          {t('print.paper_card_mismatch_file_label')}&nbsp;: {fileProfile || '—'}<br/>
          {t('print.paper_card_mismatch_paper_label')}&nbsp;&nbsp;: {paper.icc_profile || '—'}
        </div>
      )}
      {iccStatus === 'unknown' && (
        <div className="text-micro text-warn mt-2 leading-snug">
          {iccReason || t('print.paper_card_icc_unknown_default')}
        </div>
      )}
      {iccStatus === 'none' && (
        <div className="text-micro text-text-faint mt-2 leading-snug">
          {t('print.paper_card_icc_none_hint')}
        </div>
      )}
    </div>
  );
}
