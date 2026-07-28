export default function Input({ value, onChange, placeholder, mono = false, readOnly = false, maxLength, error }) {
  return (
    <input
      type="text"
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      readOnly={readOnly}
      maxLength={maxLength}
      className={`w-full px-3 py-[9px] bg-surface border rounded-md text-[12.5px] text-text-strong placeholder:text-text-faint focus:outline-none focus:bg-surface transition-colors ${
        mono ? 'font-mono tracking-normal' : ''
      } ${
        error ? 'border-danger focus:border-danger' : 'border-border-strong focus:border-accent'
      } ${readOnly ? 'cursor-default opacity-75' : ''}`}/>
  );
}
