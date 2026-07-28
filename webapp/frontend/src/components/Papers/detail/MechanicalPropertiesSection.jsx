import { useEffect, useState } from 'react';
import { Settings2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import * as api from '../../../api/client.js';

const DRY_LABELS = { 0: 'dry_time_0', 50: 'dry_time_50', 100: 'dry_time_100', 150: 'dry_time_150' };
const WHEEL_LABELS = { LOW: 'star_wheels_LOW', INTERMEDIATE: 'star_wheels_INTERMEDIATE', HIGH: 'star_wheels_HIGH' };
const RIB_LABELS = { LOW: 'pen_to_rib_LOW', HIGH: 'pen_to_rib_HIGH' };

export default function MechanicalPropertiesSection({ paper, onModify }) {
  const { t } = useTranslation();
  const [props, setProps] = useState(null);

  useEffect(() => {
    if (!paper?.mediaid) return;
    let cancelled = false;
    api.getMechanicalProperties(paper.mediaid)
      .then((data) => { if (!cancelled) setProps(data); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [paper]);

  return (
    <section className="px-5 py-4 border-b border-border-soft">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted">
          {t('papers.mech.title')}
        </h3>
        {paper?.custom && onModify && (
          <button
            type="button"
            onClick={() => onModify(paper)}
            title={t('papers.mech.modify')}
            className="flex items-center gap-1.5 px-2 py-1 rounded text-tiny font-medium text-text-muted hover:text-text-strong hover:bg-sunken transition-colors">
            <Settings2 size={11} strokeWidth={2} aria-hidden="true"/>
            {t('papers.mech.modify')}
          </button>
        )}
      </div>
      {props ? (
        <div className="bg-surface border border-border-soft rounded-lg p-3.5 space-y-2">
          <Row label={t('modify_mechanical.pen_to_rib_label')}
               value={t(`modify_mechanical.${RIB_LABELS[props.pen_to_rib] || 'pen_to_rib_LOW'}`)}
               raw={props.pen_to_rib}/>
          <Row label={t('modify_mechanical.dry_time_label')}
               value={`${t(`modify_mechanical.${DRY_LABELS[props.dry_time_factor] || 'dry_time_100'}`)} ${props.dry_time_factor}%`}
               raw={`${props.dry_time_factor}%`}/>
          <Row label={t('modify_mechanical.star_wheels_label')}
               value={t(`modify_mechanical.${WHEEL_LABELS[props.star_wheel_pos] || 'star_wheels_INTERMEDIATE'}`)}
               raw={props.star_wheel_pos}/>
          <Row label={t('modify_mechanical.cutter_label')}
               value={t(props.cutter ? 'modify_mechanical.cutter_true' : 'modify_mechanical.cutter_false')}
               raw={props.cutter ? '1' : '0'}/>
        </div>
      ) : (
        <div className="text-[11px] text-text-faint italic">{t('papers.mech.placeholder')}</div>
      )}
    </section>
  );
}

function Row({ label, value, raw }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[11.5px] text-text-muted">{label}</span>
      <div className="flex items-baseline gap-2">
        <span className="text-[11.5px] text-text-strong font-medium">{value}</span>
        <span className="text-[10px] font-mono text-text-faint">{raw}</span>
      </div>
    </div>
  );
}
