import { useTranslation } from 'react-i18next';
import { UIState } from '../../lib/state-machine.js';
import Label from '../ui/Label.jsx';
import PaperCard from '../PaperCard/PaperCard.jsx';
import ParamsSection from './ParamsSection.jsx';
import AdvancedSection from './AdvancedSection.jsx';
import PrintButton from './PrintButton.jsx';

/**
 * Right panel, 380px wide, flex-col layout with a pinned footer.
 *
 * @param {{
 *   state: string,
 *   paper: import('../../api/types').Paper | null,
 *   file:  import('../../api/types').FileWithPreview | null,
 *   params: any, onParamsChange: (p:any) => void,
 *   advanced: any, onAdvancedChange: (a:any) => void,
 *   progress: number,
 *   onPrint: () => void, onCancel: () => void,
 * }} props
 */
export default function Sidebar({
  state, paper, file,
  params, onParamsChange,
  advanced, onAdvancedChange,
  autoX = 0, autoY = 0,
  onPositionReset, centerDisabled = false, onRotate90,
  progress, geomStale = false, onPrint, onCancel,
}) {
  const { t } = useTranslation();
  const dimAll = state === UIState.D_NOPAPER;
  // The ICC dot reflects the backend's decision DIRECTLY
  // (file.info.icc_status). No derivation from the UI state — the previous
  // derivation forced iccStatus='match' as soon as state===B_READY,
  // which silently masked the backend's 'unknown' values
  // (cf. bug in Docs/freeglaz_Webapp_Roadmap.md). With no paper we reset to
  // 'none' — there is simply nothing to compare.
  const iccStatus = state === UIState.D_NOPAPER
    ? 'none'
    : (file?.info?.icc_status ?? 'none');
  // No fabricated fallback: if the file has no ICC profile, we display
  // null and PaperCard renders "—". Any fabricated value would be a silent
  // assumption (cf. core mission, §1 roadmap).
  const fileProfile = file?.info?.icc_profile || null;
  const iccReason   = file?.preview?.icc_match_reason || null;
  // The paper profile name comes from the backend preview (/api/print/preview
  // returns paper_icc_name via authoritative SOAP get_profile). If no file is
  // loaded yet, paper.icc_profile is null — the badge falls to 'none' (neutral).
  const paperWithIcc = paper ? {
    ...paper,
    icc_profile: file?.preview?.paper_icc_name ?? paper.icc_profile,
  } : null;

  return (
    <aside className="w-[380px] bg-bg border-l border-border-soft flex flex-col">
      <header className="px-5 pt-4 pb-3.5 flex items-center gap-2.5">
        <div className="w-[18px] h-[18px] rounded-[5px]"
             style={{ background: 'conic-gradient(from 90deg, var(--accent), var(--text-strong) 75%, var(--accent))' }}/>
        <h1 className="text-[13.5px] font-semibold text-text-strong tracking-tight whitespace-nowrap">
          {t('print.sidebar_app_title')}
        </h1>
      </header>

      <div className="flex-1 px-5 overflow-y-auto no-scrollbar">
        <Label>{t('print.sidebar_paper_loaded')}</Label>
        <PaperCard
          paper={paperWithIcc}
          iccStatus={iccStatus}
          fileProfile={fileProfile}
          iccReason={iccReason}/>

        <div className="h-5"/>
        <ParamsSection
          value={params}
          onChange={onParamsChange}
          disabled={dimAll}
          glossSupported={paper?.gloss_enhancer_supported === true}/>

        <div className="h-2"/>
        <AdvancedSection
          value={advanced}
          onChange={onAdvancedChange}
          autoX={autoX}
          autoY={autoY}
          onPositionReset={onPositionReset}
          centerDisabled={centerDisabled}
          onRotate90={onRotate90}
          disabled={dimAll}/>

        <div className="h-6"/>
      </div>

      <PrintButton state={state} progress={progress} geomStale={geomStale} onPrint={onPrint} onCancel={onCancel}/>
    </aside>
  );
}
