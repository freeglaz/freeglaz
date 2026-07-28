import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import OriginBadge, { originKeyFromInspection } from './OriginBadge.jsx';
import TRCBadge from './TRCBadge.jsx';
import TagPopover from './TagPopover.jsx';

// Profile source propagated down to the TagPopover via explicit prop drilling
// (the LutBlock in the popover needs it to fetch the LUT scatter).

/**
 * Identity view — 5 informational cards.
 * Single source: the ``inspection`` dict returned by /api/profiles/inspect.
 *
 * UX fixes:
 *  1. Profile type shown in plain text (device_class → i18n label).
 *  2. Gamut class hidden for non-workspace profiles.
 *  3. Clickable tags → contextual popover (TagPopover).
 *  4. Volumes annotated "Lab³ units".
 */
export default function IdentityView({ inspection, source, onView3D }) {
  const { t, i18n } = useTranslation();
  if (!inspection) {
    return (
      <div className="text-text-faint italic px-6 py-8">
        {t('inspector.loading')}
      </div>
    );
  }

  const originKey = originKeyFromInspection(inspection);
  const trc = inspection.trc || {};
  const taxonomy = inspection.taxonomy || {};
  const gamut = inspection.gamut || {};
  const trcFamily = trc.family?.effective ?? 'unknown_custom';
  const primaryFamily = taxonomy.primary_family?.effective ?? 'custom';
  const gamutClass = taxonomy.gamut_class?.effective ?? 'custom';
  const wp = taxonomy.whitepoint?.effective ?? 'custom';
  const wpXy = taxonomy.whitepoint_xy;
  const consistentTrc = trc.consistent_across_channels !== false;

  const deviceClass = inspection.device_class || '';
  const deviceClassLabel = _deviceClassLabel(deviceClass, t);
  // Fix 2: gamut class only makes sense for working
  // spaces and displays. For a printing, scanner, camera profile,
  // etc., the volume → category classification is misleading.
  const showGamutClass = deviceClass === 'spac' || deviceClass === 'mntr';

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 px-6 py-5">
      {/* Card 1 — Identity */}
      <Card title={t('inspector.card_identity')}>
        <Row label={t('inspector.field_description')}
             value={inspection.description}
             mono/>
        <Row label={t('inspector.field_device_class')}
             value={deviceClassLabel}/>
        <Row label={t('inspector.field_icc_version')}
             value={inspection.icc_version}
             mono/>
        <Row label={t('inspector.field_size')}
             value={inspection.file_size
                    ? `${inspection.file_size.toLocaleString(i18n.language)} bytes`
                    : null}
             mono/>
        <Row label={t('inspector.field_copyright')}
             value={inspection.copyright}/>
      </Card>

      {/* Card 2 — Origin */}
      <Card title={t('inspector.card_origin')}>
        <div className="mb-2.5">
          <OriginBadge originKey={originKey}/>
        </div>
        <Row label={t('inspector.field_cmm')}
             value={inspection.creator_signature || inspection.cmm}
             mono/>
        <Row label={t('inspector.field_manufacturer')}
             value={inspection.manufacturer_desc}/>
        <Row label={t('inspector.field_model')}
             value={inspection.model_desc}/>
        <Row label={t('inspector.field_profile_type')}
             value={inspection.profile_type_guess}/>
        {inspection.is_hp_ingenium && inspection.hp91_description && (
          <Row label={t('inspector.field_hp91_description')}
               value={inspection.hp91_description}/>
        )}
      </Card>

      {/* Card 3 — Color space */}
      <Card title={t('inspector.card_color_space')}>
        <Row label={t('inspector.field_color_space')}
             value={inspection.color_space || '—'}
             mono/>
        <Row label={t('inspector.field_pcs')}
             value={inspection.pcs || '—'}
             mono/>
        <div className="flex items-center justify-between gap-3 py-1">
          <span className="text-text-muted text-xs2">
            {t('inspector.field_trc_family')}
          </span>
          <TRCBadge family={trcFamily}/>
        </div>
        {trc.per_channel && Object.keys(trc.per_channel).length > 0 && (
          <Row label={t(consistentTrc
                        ? 'inspector.field_trc_consistent'
                        : 'inspector.field_trc_variation')}
               value={_perChannelHint(trc.per_channel, t)}/>
        )}
        <Row label={t('inspector.field_whitepoint')}
             value={wpXy ? `${wp} (x=${wpXy[0].toFixed(4)}, y=${wpXy[1].toFixed(4)})` : wp}
             mono/>
        <Row label={t('inspector.field_primary_family')}
             value={primaryFamily}/>
        {showGamutClass && (
          <Row label={t('inspector.field_gamut_class')}
               value={gamutClass}/>
        )}
      </Card>

      {/* Card 4 — Gamut */}
      <Card title={t('inspector.card_gamut')}>
        {gamut.status === 'ok' || gamut.status === undefined
          ? (
            <>
              <Row label={t('inspector.field_volume_perceptual')}
                   value={_fmtVolume(gamut.perceptual, i18n.language)}
                   tooltip={t('inspector.volume_tooltip')}
                   mono/>
              <Row label={t('inspector.field_volume_relative')}
                   value={_fmtVolume(gamut.relative, i18n.language)}
                   tooltip={t('inspector.volume_tooltip')}
                   mono/>
              {gamut.delta_per_rel_pct !== undefined && (
                <Row label={t('inspector.field_delta_per_rel')}
                     value={`${gamut.delta_per_rel_pct >= 0 ? '+' : ''}${gamut.delta_per_rel_pct}%`}
                     mono/>
              )}
              {/* Interpretation sentence REMOVED: based on the volume gap
                  (heuristic "close volumes = no mapping") → misleading
                  (a custom perceptual mapping can reorganize hues without
                  changing the volume, cf. HP91 color_mapping tag). The factual
                  "Perceptual vs relative" figure above is enough. */}
              <p className="text-tiny text-text-faint mt-1.5 italic">
                {t('inspector.gamut_reserve_note')}
              </p>
            </>
          )
          : (
            <p className="text-xs text-text-faint italic">
              {gamut.status === 'deps_missing'
                ? t('inspector.gamut_missing_deps')
                : t('inspector.gamut_failed')}
            </p>
          )}
      </Card>

      {/* Card 5 — Technical details (compact, span 2) */}
      <Card title={t('inspector.card_tech_details')} compact wide>
        <Row label={t('inspector.field_n_tags')}
             value={inspection.n_tags}
             mono/>
        <TagsList tagsDetails={inspection.tags_details || []} source={source}
                  onView3D={onView3D} hp91={inspection.internals?.hp91}/>
      </Card>
    </div>
  );
}


function TagsList({ tagsDetails, source, onView3D, hp91 }) {
  const [openSig, setOpenSig] = useState(null);
  if (!tagsDetails || tagsDetails.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-2">
      {tagsDetails.map((tag, i) => {
        const sigKey = `${tag.signature}-${i}`;
        const active = openSig === sigKey;
        return (
          <button
            key={sigKey}
            type="button"
            onClick={() => setOpenSig(active ? null : sigKey)}
            title={`${tag.type} · ${tag.size} bytes`}
            className={`inline-block px-1.5 py-px text-[10px] font-mono rounded
                        border transition-colors
                        ${active
                          ? 'bg-accent/10 text-accent border-accent/40'
                          : 'bg-sunken text-text-muted border-border-soft hover:text-text-strong hover:border-border-strong'}`}>
            {tag.signature}
          </button>
        );
      })}
      {tagsDetails.map((tag, i) => {
        const sigKey = `${tag.signature}-${i}`;
        if (openSig !== sigKey) return null;
        return (
          <TagPopover
            key={`popover-${sigKey}`}
            tag={tag}
            source={source}
            hp91={hp91}
            onClose={() => setOpenSig(null)}
            onView3D={onView3D ? (entryKey) => {
              setOpenSig(null);
              onView3D(entryKey);
            } : undefined}/>
        );
      })}
    </div>
  );
}


function Card({ title, children, compact, wide }) {
  return (
    <section
      className={`bg-surface border border-border-soft rounded-xl ${compact ? 'p-4' : 'p-5'}
                  ${wide ? 'lg:col-span-2' : ''}`}>
      <h3 className="text-[10px] font-bold tracking-[0.10em] uppercase text-accent mb-3 font-mono">
        {title}
      </h3>
      <div className="space-y-1.5">
        {children}
      </div>
    </section>
  );
}


function Row({ label, value, mono, tooltip }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-text-muted text-xs2 shrink-0">{label}</span>
      <span
        title={tooltip || undefined}
        className={`text-text-strong text-right break-all
                    ${mono ? 'font-mono text-[12.5px]' : 'text-xs2'}
                    ${tooltip ? 'cursor-help' : ''}`}>
        {value}
      </span>
    </div>
  );
}


function _deviceClassLabel(deviceClass, t) {
  const m = {
    prtr: t('inspector.device_class.prtr'),
    mntr: t('inspector.device_class.mntr'),
    scnr: t('inspector.device_class.scnr'),
    cmra: t('inspector.device_class.cmra'),
    spac: t('inspector.device_class.spac'),
    link: t('inspector.device_class.link'),
    abst: t('inspector.device_class.abst'),
    nmcl: t('inspector.device_class.nmcl'),
  };
  return m[deviceClass] || deviceClass || '—';
}


function _fmtVolume(v, lang) {
  if (v === null || v === undefined) return null;
  const n = Math.round(v).toLocaleString(lang === 'fr' ? 'fr-FR' : 'en-US');
  // Fix 4: Lab³ units annotation
  return (
    <>
      {n} <span className="text-text-muted text-[11px]">Lab³</span>
    </>
  );
}


function _perChannelHint(perChannel, t) {
  const parts = [];
  for (const [ch, info] of Object.entries(perChannel)) {
    const fam = info.family || '?';
    const gamma = info.gamma_estimate;
    parts.push(`${ch.toUpperCase()}: ${fam}${gamma ? ` (γ ${gamma})` : ''}`);
  }
  return parts.join(' · ');
}
