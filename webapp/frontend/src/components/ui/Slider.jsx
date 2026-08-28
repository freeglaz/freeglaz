/**
 * Slider — minimal themed continuous range control (from scratch, no external dep).
 *
 * Native <input type="range"> styled with the HP-blue accent. Emits a float via
 * onChange. The caller displays the numeric value (traceability doctrine).
 */
export default function Slider({ value, onChange, min = 0, max = 1, step = 0.01, disabled = false }) {
  return (
    <input
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(parseFloat(e.target.value))}
      className="w-full h-1.5 cursor-pointer appearance-none rounded-full bg-border
                 accent-hp-blue disabled:opacity-40 disabled:cursor-not-allowed"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
    />
  );
}
