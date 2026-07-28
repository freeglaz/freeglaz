import { Check } from 'lucide-react';

export default function Stepper({ steps, currentId }) {
  const idx = steps.findIndex((s) => s.id === currentId);

  return (
    <div className="flex items-center gap-0 px-[22px] -mt-0.5 -mb-1">
      {steps.map((s, i) => {
        const done = i < idx;
        const active = i === idx;
        return (
          <div key={s.id} className={`flex items-center ${i < steps.length - 1 ? 'flex-1' : ''}`}>
            <div className="inline-flex items-center gap-1.5">
              <span className={`w-[18px] h-[18px] rounded-full flex-shrink-0 flex items-center justify-center text-[9.5px] font-bold font-mono ${
                done
                  ? 'bg-success text-white'
                  : active
                    ? 'bg-accent text-white'
                    : 'border-[1.4px] border-border-strong'
              }`}>
                {done ? (
                  <Check size={9} strokeWidth={2.2}/>
                ) : (
                  <span className={active ? 'text-white' : 'text-text-faint'}>{i + 1}</span>
                )}
              </span>
              <span className={`text-[10.5px] tracking-[0.04em] uppercase whitespace-nowrap ${
                active ? 'font-bold text-accent' : done ? 'font-medium text-text-strong' : 'font-medium text-text-faint'
              }`}>
                {s.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div className="flex-1 h-px mx-[11px] relative">
                <div className={`absolute inset-0 transition-colors duration-300 ${
                  done ? 'bg-success' : active ? 'bg-gradient-to-r from-accent to-border-soft' : 'bg-border-soft'
                }`}/>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
