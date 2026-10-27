// The banana mascot. Until the 3D renders arrive it is the inline SVG of the mockup.
// TODO(#389): put idle.png and think.png into web/public/mascot/ and set RENDERS = true.
const RENDERS = false;

export function Mascot({ size = 30, state = "idle" }: { size?: number; state?: "idle" | "thinking" }) {
  const inner = Math.round(size * 0.68);
  return (
    <span
      className={`mascot${state === "thinking" ? " thinking" : ""}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {RENDERS ? (
        <img src={`/mascot/${state === "thinking" ? "think" : "idle"}.png`} width={inner} height={inner} alt="" />
      ) : (
        <svg width={inner} height={inner} viewBox="0 0 64 64">
          <path d="M14 46c10 8 30 6 38-12 2-5 2-11 0-16-2 10-10 22-24 24-6 1-11 1-14 4z" fill="#141414" />
          <path d="M50 18c1-3 1-6-1-8" stroke="#141414" strokeWidth="4" strokeLinecap="round" fill="none" />
          <circle cx="30" cy="36" r="2.6" fill="#FFD800" />
          <circle cx="38" cy="33" r="2.6" fill="#FFD800" />
        </svg>
      )}
    </span>
  );
}
