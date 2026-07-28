export default function StepHero({ picto, children }) {
  return (
    <div className="flex flex-col items-center gap-3.5 pt-6 pb-[18px]">
      {picto}
      <div className="text-center max-w-[440px]">{children}</div>
    </div>
  );
}
