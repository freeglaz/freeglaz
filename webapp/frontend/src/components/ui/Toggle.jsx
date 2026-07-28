// ON/OFF toggle. ON = glaz accent (DS spec). Not configurable, that's the rule.
export default function Toggle({ on, onChange, disabled = false }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onChange?.(!on)}
      className={`relative w-[34px] h-[20px] rounded-full transition-colors ${on ? 'bg-accent' : 'bg-line-strong'} ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}>
      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${on ? 'left-[16px]' : 'left-0.5'}`}/>
    </button>
  );
}
