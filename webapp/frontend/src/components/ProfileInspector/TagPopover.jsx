import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { ArrowRight, ChevronDown, ChevronRight } from 'lucide-react';
import Sparkline, { channelColor } from './Sparkline.jsx';

// Mapping LUT/gamt signature → View3D selector entry key.
const _LUT_TO_VIEW3D_KEY = {
  A2B0: 'A2B0', A2B1: 'A2B1', A2B2: 'A2B2',
  B2A0: 'B2A0', B2A1: 'B2A1', B2A2: 'B2A2',
  gamt: 'gamt',
};

/**
 * Tag popover — centered in the parent modal (which is itself
 * centered on the viewport), not anchored to the clicked chip. Ensures the
 * content stays fully visible regardless of the position of the
 * triggering chip, even at the very bottom of the Technical details card.
 *
 * Portal to document.body to escape containing blocks
 * (transform/filter on ancestors). Closing: click outside + Escape.
 */
export default function TagPopover({ tag, onClose, source, onView3D, hp91 }) {
  const { t } = useTranslation();
  const ref = useRef(null);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose?.();
      }
    };
    const onClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        onClose?.();
      }
    };
    window.addEventListener('keydown', onKey);
    // setTimeout 0 to avoid capturing the click that opened the popover
    const t0 = setTimeout(() => window.addEventListener('mousedown', onClick), 0);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onClick);
      clearTimeout(t0);
    };
  }, [onClose]);

  const name = tag.name;
  const decoded = tag.decoded;

  // Fixed centered on the viewport. The ProfileInspector modal is also
  // centered on the viewport, so viewport-center ≡ modal-center. The
  // popover stays entirely within the modal, never truncated.
  // z-[110] to sit above the modal (z-[100]).
  // Width 440px (fix) to accommodate the 180×80 sparklines
  // stacked vertically with the channel label on the left.
  const isLut = tag.decoded?.kind === 'lut';
  const width = isLut ? 'w-[440px]' : 'w-[360px]';
  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-label={t('inspector.tag_popover_aria', { signature: tag.signature })}
      className={`fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2
                 z-[110] ${width} max-h-[85vh] overflow-y-auto
                 bg-surface border border-border-soft rounded-lg shadow-2xl
                 p-4 text-text-strong`}>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="font-mono text-sm font-semibold text-accent">
          {tag.signature}
        </span>
        {name && (
          <span className="text-xs2 text-text-muted leading-tight">
            {name}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-tiny mb-2.5
                      pb-2.5 border-b border-border-soft">
        <Meta label={t('inspector.popover_field_type')}
              value={tag.type || '—'} mono/>
        <Meta label={t('inspector.popover_field_size')}
              value={`${tag.size.toLocaleString()} B`} mono/>
      </div>
      <DecodedBlock decoded={decoded} tag={tag} source={source} onView3D={onView3D} hp91={hp91}/>
    </div>,
    document.body,
  );
}


function DecodedBlock({ decoded, tag, onView3D, hp91 }) {
  const { t } = useTranslation();
  if (!decoded) {
    return (
      <p className="text-tiny text-text-faint italic">
        {t('inspector.popover_no_decode')}
      </p>
    );
  }
  switch (decoded.kind) {
    case 'text':
      return (
        <div>
          <p className="text-xs2 font-mono break-words whitespace-pre-wrap
                        leading-snug max-h-[200px] overflow-y-auto">
            {decoded.text}
          </p>
          {decoded.truncated && (
            <p className="text-tiny text-text-faint italic mt-1.5">
              {t('inspector.popover_text_truncated',
                 { length: decoded.full_length })}
            </p>
          )}
        </div>
      );
    case 'xyz':
      return (
        <div className="space-y-1 text-tiny font-mono">
          <Row label="X" value={decoded.X?.toFixed(6)}/>
          <Row label="Y" value={decoded.Y?.toFixed(6)}/>
          <Row label="Z" value={decoded.Z?.toFixed(6)}/>
          {decoded.xy && (
            <Row label="xy" value={`(${decoded.xy[0].toFixed(4)}, ${decoded.xy[1].toFixed(4)})`}/>
          )}
        </div>
      );
    case 'trc':
      return (
        <div className="space-y-1 text-tiny font-mono">
          <Row label={t('inspector.popover_trc_type')}
               value={decoded.method || decoded.type}/>
          <Row label={t('inspector.popover_trc_family')}
               value={decoded.family}/>
          {decoded.gamma_single !== undefined && (
            <Row label={t('inspector.popover_trc_gamma_single')}
                 value={decoded.gamma_single}/>
          )}
          {decoded.gamma_estimate !== undefined && decoded.gamma_estimate !== null && (
            <Row label={t('inspector.popover_trc_gamma_est')}
                 value={decoded.gamma_estimate}/>
          )}
          {decoded.n_entries !== undefined && decoded.n_entries > 1 && (
            <Row label={t('inspector.popover_trc_n_entries')}
                 value={decoded.n_entries}/>
          )}
        </div>
      );
    case 'lut':
      return <LutBlock decoded={decoded} tag={tag} onView3D={onView3D}/>;
    case 'hp91':
      return <Hp91Block hp91={hp91} sizeBytes={decoded.decompressed_bytes}/>;
    default:
      return (
        <p className="text-tiny text-text-faint italic">
          {t('inspector.popover_no_decode')}
        </p>
      );
  }
}


function Row({ label, value }) {
  if (value === undefined || value === null || value === '') return null;
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-text-muted">{label}</span>
      <span className="text-text-strong">{value}</span>
    </div>
  );
}


/**
 * Decoding of the proprietary HP91 tag (moved from the former Internals tab —
 * HP91 IS a tag, its place is in the tag explorer). Pre-decoded backend
 * data: inspection.internals.hp91 (paper/ink/printmode/clc_state/media_id +
 * raw fields). If not decoded/absent: size only.
 */
function Hp91Block({ hp91, sizeBytes }) {
  const { t } = useTranslation();
  const [rawOpen, setRawOpen] = useState(false);

  if (!hp91 || !hp91.present) {
    return (
      <p className="text-tiny font-mono text-text-muted">
        {sizeBytes != null
          ? t('inspector.popover_hp91_size', { bytes: sizeBytes.toLocaleString() })
          : t('inspector.internals.hp91_absent')}
      </p>
    );
  }
  const dec = hp91.decoded || {};
  const rawFields = hp91.raw_fields || {};
  const rawKeys = Object.keys(rawFields).sort();

  return (
    <div className="space-y-1 text-tiny">
      <Hp91Row label={t('inspector.internals.hp91_signature')} value={hp91.signature} mono/>
      <Hp91Row label={t('inspector.internals.hp91_description')} value={hp91.description}/>
      <Hp91Row label={t('inspector.internals.hp91_cluster')} value={hp91.cluster_md5} mono/>
      <Hp91Row label={t('inspector.internals.hp91_paper')} value={dec.paper}/>
      <Hp91Row label={t('inspector.internals.hp91_ink')} value={dec.ink}/>
      <Hp91Row label={t('inspector.internals.hp91_printmode')} value={dec.printmode}/>
      <Hp91Row label={t('inspector.internals.hp91_clc_state')} value={dec.clc_state}/>
      <Hp91Row label={t('inspector.internals.hp91_media_id')} value={dec.media_id} mono/>

      {rawKeys.length > 0 && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setRawOpen(!rawOpen)}
            className="flex items-center gap-1.5 text-tiny text-accent
                       hover:text-accent/80 transition-colors">
            {rawOpen ? <ChevronDown size={12}/> : <ChevronRight size={12}/>}
            {t('inspector.internals.hp91_raw_fields', { count: rawKeys.length })}
          </button>
          {rawOpen && (
            <div className="mt-1.5 max-h-[240px] overflow-y-auto bg-sunken/40
                            border border-border-soft rounded-md p-2">
              <table className="w-full table-fixed text-[10.5px] font-mono">
                <tbody>
                  {rawKeys.map((k) => (
                    <tr key={k} className="border-b border-border-soft/40 last:border-0 align-top">
                      <td className="w-2/5 text-text-muted pr-2 py-0.5 align-top break-all">{k}</td>
                      <td className="text-text-strong py-0.5 align-top break-all whitespace-pre-wrap">{rawFields[k]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function Hp91Row({ label, value, mono }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="flex items-baseline justify-between gap-2 py-0.5">
      <span className="text-text-muted shrink-0">{label}</span>
      <span className={`text-text-strong text-right break-all ${mono ? 'font-mono' : ''}`}>
        {value}
      </span>
    </div>
  );
}


/**
 * Enriched LUT block — 3×3 matrix, input/output sparklines
 * stacked vertically, CLUT dimensions between the two groups.
 * Special gamt case: single block in large format (1D PCS LUT → indicator).
 */
function LutBlock({ decoded, tag, onView3D }) {
  const { t } = useTranslation();
  if (decoded.is_gamut_check) {
    return <GamtBlock decoded={decoded} onView3D={onView3D}/>;
  }

  const lutType = decoded.lut_type || '—';
  const inCh = decoded.input_channels;
  const outCh = decoded.output_channels;
  const dims = decoded.clut_dimensions;
  const matrix = decoded.matrix_3x3;
  const inputCurves = decoded.input_curves || [];
  const outputCurves = decoded.output_curves || [];

  const view3dKey = _LUT_TO_VIEW3D_KEY[tag?.signature];
  return (
    <div className="space-y-3.5">
      {/* "Visualize in 3D" button at the top */}
      {onView3D && view3dKey && (
        <View3DButton onClick={() => onView3D(view3dKey)}/>
      )}

      {/* Compact technical header */}
      <div className="text-tiny font-mono leading-snug">
        <span className="text-text-strong">{lutType}</span>
        {decoded.precision_bits && (
          <span className="text-text-muted"> · {decoded.precision_bits} bits</span>
        )}
        {inCh !== undefined && outCh !== undefined && (
          <span className="text-text-muted">
            {' · '}{inCh}×{outCh} ch
          </span>
        )}
      </div>

      {/* 3×3 matrix if present — placed between the header and the input
          curves (hierarchy: type → matrix → input → CLUT → output) */}
      {matrix && (
        <div>
          <SectionTitle>{t('inspector.popover_lut_matrix')}</SectionTitle>
          <table className="font-mono text-[10.5px] text-text-strong">
            <tbody>
              {matrix.map((row, ri) => (
                <tr key={ri}>
                  {row.map((v, ci) => (
                    <td key={ci} className="pr-2 py-px text-right">
                      {v.toFixed(4)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Input curves stacked vertically */}
      {inputCurves.length > 0 && (
        <CurvesGroup
          title={t('inspector.popover_lut_input_curves')}
          curves={inputCurves}/>
      )}

      {/* CLUT — dimensions only; the 3D rendering is in the 3D View */}
      {dims && dims.length > 0 && (
        <div>
          <SectionTitle>{t('inspector.popover_lut_clut')}</SectionTitle>
          <p className="text-xs2 font-mono text-text-strong">
            {dims.join(' × ')}
          </p>
        </div>
      )}

      {/* Output curves stacked vertically */}
      {outputCurves.length > 0 && (
        <CurvesGroup
          title={t('inspector.popover_lut_output_curves')}
          curves={outputCurves}/>
      )}
    </div>
  );
}


/** Special gamt case: gamut indicator (declared boundary). */
function GamtBlock({ decoded, onView3D }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      {onView3D && (
        <View3DButton onClick={() => onView3D('gamt')}/>
      )}
      <SectionTitle>{t('inspector.popover_gamt_title')}</SectionTitle>
      <p className="text-tiny text-text-muted leading-relaxed">
        {t('inspector.popover_gamt_description')}
      </p>
      <div className="space-y-1 text-tiny font-mono pt-1
                      border-t border-border-soft">
        <Row label={t('inspector.popover_lut_type')}
             value={decoded.underlying_type || 'gamt'}/>
        {decoded.precision_bits && (
          <Row label={t('inspector.popover_lut_precision')}
               value={`${decoded.precision_bits} bits`}/>
        )}
        {decoded.grid_points !== undefined && (
          <Row label={t('inspector.popover_lut_grid')}
               value={`${decoded.grid_points}³`}/>
        )}
      </div>
    </div>
  );
}


function View3DButton({ onClick }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded
                 bg-accent/10 text-accent text-xs2 font-medium
                 hover:bg-accent/20 transition-colors">
      {t('inspector.popover_view_in_3d')}
      <ArrowRight size={12} strokeWidth={2.2} aria-hidden="true"/>
    </button>
  );
}


function CurvesGroup({ title, curves }) {
  return (
    <div>
      <SectionTitle>{title}</SectionTitle>
      <div className="flex flex-col gap-2">
        {curves.map((c, i) => (
          <div key={i} className="flex items-center gap-3">
            <span
              className="font-mono font-semibold text-sm w-6 text-center shrink-0"
              style={{ color: channelColor(c.channel) }}
              aria-hidden="true">
              {c.channel}
            </span>
            <Sparkline
              samples={c.samples}
              color={channelColor(c.channel)}
              linear={c.linear}
              width={180}
              height={80}
              label={`${c.channel} curve, n=${c.n_samples}`}/>
            <span
              className="text-tiny font-mono text-text-faint"
              title={`${c.n_samples} samples`}>
              n={c.n_samples}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}


function SectionTitle({ children }) {
  return (
    <div className="text-[11px] font-semibold uppercase tracking-wider
                    text-text-strong mb-1.5">
      {children}
    </div>
  );
}


function Meta({ label, value, mono }) {
  return (
    <div>
      <div className="text-text-faint text-[9px] uppercase tracking-wider">{label}</div>
      <div className={`${mono ? 'font-mono' : ''} text-text-strong text-xs2`}>{value}</div>
    </div>
  );
}
