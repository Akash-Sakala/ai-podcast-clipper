/**
 * The mark: a sound wave with the part inside a vertical 9:16 frame in amber,
 * i.e. a short clip cut from a long recording. Grey bars follow the text colour.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      strokeLinecap="round"
      aria-hidden
      className={className}
    >
      <path
        d="M3 14v4M6.5 11.5v9M25.5 12v8M29 14.5v3"
        stroke="currentColor"
        strokeOpacity="0.5"
        strokeWidth="2.25"
      />
      <rect
        x="10"
        y="4.5"
        width="12"
        height="23"
        rx="3.5"
        stroke="var(--tally)"
        strokeWidth="2"
      />
      <path
        d="M13 12.5v7M16 9v14M19 11.5v9"
        stroke="var(--tally)"
        strokeWidth="2.25"
      />
    </svg>
  );
}
