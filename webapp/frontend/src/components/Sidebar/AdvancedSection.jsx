import { useState } from 'react';
import { ChevronRight, Crosshair, RotateCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import Toggle from '../ui/Toggle.jsx';
import Select from '../ui/Select.jsx';

function Row({ label, children }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <div className="text-sm2 text-text-strong font-normal whitespace-nowrap">{label}</div>
      <div>{children}</div>
    </div>
  );
}

function MmInput({ value, onChange, hint = '' }) {
  return (
    <div className={`flex items-center gap-1.5 font-mono text-xs bg-sunken px-2.5 py-1 rounded
                     ${hint ? 'text-text-faint italic' : 'text-text-strong'}`}>
      <input
        type="text"
        value={value.toFixed(1)}
        onChange={(e) => { const n = parseFloat(e.target.value); if (!isNaN(n)) onChange?.(n); }}
        className="w-12 bg-transparent outline-none text-right tabular-nums"
        title={hint || undefined}/>
      <span className="text-text-faint">mm</span>
    </div>
  );
}

/**
 * Collapsible section. Collapsed by default — 90% of prints never touch it.
 *
 * **Position X / Position Y** (bug resolved): **absolute** values in mm
 * from the top-left corner of the sheet, consistent with native Z9 PJL and
 * graphic standards. When `value.positionX` is null (initial state / after a
 * "Center" click), the inputs display in italic the auto-centered position
 * computed from `autoX` / `autoY`.
 *
 * @param {{
 *   value: { positionX: number|null, positionY: number|null, ... },
 *   onChange: (patch:any) => void,
 *   autoX?: number, autoY?: number,
 *   onPositionReset?: () => void,
 *   centerDisabled?: boolean,
 *   disabled?: boolean,
 * }} props
 *
 * `centerDisabled` drives the "Center" button (computed in App, roll-aware):
 * sheet = disabled at the auto position; roll = disabled once X is on the
 * horizontal center. It is distinct from `isAuto` (which only styles the
 * italic placeholder of the position inputs).
 */
export default function AdvancedSection({
  value, onChange,
  autoX = 0, autoY = 0,
  onPositionReset, centerDisabled = false, onRotate90,
  disabled = false,
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const upd = (patch) => onChange?.({ ...value, ...patch });

  // If the position has never been touched by the user (null), we display
  // the auto center in italic as an informative placeholder. As soon as a
  // value is typed, we store the number as-is.
  const isAuto = value.positionX == null && value.positionY == null;
  const dispX  = value.positionX ?? autoX;
  const dispY  = value.positionY ?? autoY;

  return (
    <div className={disabled ? 'opacity-50 pointer-events-none' : ''}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1.5 text-xs2 text-text-muted font-semibold tracking-wider uppercase py-2 pt-2 pb-3 hover:text-text-strong w-full">
        <ChevronRight size={11} strokeWidth={2.5} aria-hidden="true" className={`transition-transform ${open ? 'rotate-90' : ''}`}/>
        {t('print.advanced_label')}
      </button>
      {open && (
        <div className="pb-2">
          <Row label={t('print.advanced_position_x')}>
            <MmInput
              value={dispX}
              onChange={(v) => upd({ positionX: v })}
              hint={isAuto ? t('print.advanced_hint_auto_centered') : ''}/>
          </Row>
          <Row label={t('print.advanced_position_y')}>
            <MmInput
              value={dispY}
              onChange={(v) => upd({ positionY: v })}
              hint={isAuto ? t('print.advanced_hint_auto_centered') : ''}/>
          </Row>
          <Row label={t('print.advanced_center_image')}>
            <button
              type="button"
              onClick={() => onPositionReset?.()}
              disabled={centerDisabled}
              title={t('print.advanced_center_tooltip')}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-sunken
                         hover:bg-sunken-deep text-xs2 text-text-strong font-medium
                         disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
              <Crosshair size={11} strokeWidth={2}/>
              {t('print.advanced_center_button')}
            </button>
          </Row>
          <Row label={t('print.advanced_orientation')}>
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs2 text-text-strong tabular-nums w-8 text-right">
                {value.orientation || 0}°
              </span>
              {/* Source indicator: auto-fit (initial suggestion) vs manual
                  (the user rotated → takes priority over auto). Hidden at an
                  untouched 0° (default neutral state). */}
              {(value.orientation || value.orientationTouched) ? (
                <span className="text-[10px] text-text-faint italic">
                  {value.orientationTouched
                    ? t('print.advanced_orientation_manual')
                    : t('print.advanced_orientation_auto')}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => onRotate90?.()}
                title={t('print.advanced_orientation_tooltip')}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-sunken
                           hover:bg-sunken-deep text-xs2 text-text-strong font-medium
                           transition-colors ml-auto">
                <RotateCw size={11} strokeWidth={2}/>
                {t('print.advanced_orientation_button')}
              </button>
            </div>
          </Row>
          <Row label={t('print.advanced_max_detail')}><Toggle on={value.maxDetail} onChange={(v) => upd({ maxDetail: v })}/></Row>
          <Row label={t('print.advanced_dry_time')}>
            <Select value={value.dryTime} onChange={(v) => upd({ dryTime: v })}
                    options={[
                      { value: 'Normal', label: t('print.advanced_dry_time_normal') },
                      { value: 'Étendu', label: t('print.advanced_dry_time_extended') },
                    ]}/>
          </Row>
          <Row label={t('print.advanced_render_mode')}>
            <Select value={value.renderMode} onChange={(v) => upd({ renderMode: v })}
                    options={[
                      { value: 'Couleur', label: t('print.advanced_render_color') },
                      { value: 'Niveaux de gris', label: t('print.advanced_render_grayscale') },
                    ]}/>
          </Row>
        </div>
      )}
    </div>
  );
}
