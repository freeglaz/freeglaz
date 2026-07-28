// Uppercase tracked label — caps the sidebar sections.
export default function Label({ children, className = '' }) {
  return (
    <div className={`text-micro font-medium tracking-[0.08em] uppercase text-text-faint mb-2 ${className}`}>
      {children}
    </div>
  );
}
