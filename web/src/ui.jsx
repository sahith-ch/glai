// Small shared pieces: icons, avatars, badges and number formatting.

export const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
export const score = (x) => (x == null ? "–" : Math.round(x));
export const initials = (name) => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");

/** "top 15%", or plainly "only one ranked" - "#1 of 1, top 100%" reads like last place. */
export const topText = (p) => (p.total === 1 ? "only one ranked" : `top ${p.top_percent}%`);

// muted, solid colours - like initials in a mail client, not a gradient sticker
const COLOURS = ["#3d5a80", "#6b4f3a", "#4a6b57", "#7a4b5c", "#5b5f97", "#806a3c", "#3f6b73", "#6d5a7a"];
function hue(name) {
  let h = 0;
  for (const ch of name || "") h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return COLOURS[h % COLOURS.length];
}

export function Avatar({ name, size = "md" }) {
  return (
    <span className={`avatar avatar-${size}`} aria-hidden="true" style={{ background: hue(name) }}>
      {initials(name)}
    </span>
  );
}

export function TierBadge({ tier }) {
  return <span className={`tier tier-${tier.toLowerCase()}`}>{tier}</span>;
}

export function DemoBadge() {
  return <span className="badge" title="A sample profile, not a real person">Demo</span>;
}

const PATHS = {
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  pin: <><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21Z" /><circle cx="12" cy="9.5" r="2.5" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  checkCircle: <><circle cx="12" cy="12" r="9" /><path d="m8 12.5 3 3 5-6" /></>,
  arrow: <path d="M5 12h14m-5-6 6 6-6 6" />,
  back: <path d="M19 12H5m5-6-6 6 6 6" />,
  trophy: <><path d="M8 4h8v5a4 4 0 0 1-8 0V4Z" /><path d="M8 6H5v1a3 3 0 0 0 3 3m8-4h3v1a3 3 0 0 1-3 3M12 13v4m-4 3h8" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m4 7 8 6 8-6" /></>,
  link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  code: <path d="m8 8-4 4 4 4m8-8 4 4-4 4m-6 3 4-14" />,
  bolt: <path d="M13 3 5 14h6l-1 7 8-11h-6l1-7Z" />,
  flask: <><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3" /><path d="M7.5 14h9" /></>,
  graph: <><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="8" r="2.5" /><circle cx="10" cy="18" r="2.5" /><path d="m8.2 7 7.4.6M7 8.3l2 7.4m2.5.7 5-6.4" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  filter: <path d="M4 5h16l-6 8v5l-4 2v-7L4 5Z" />,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4m8-4v4" /></>,
  sparkle: <path d="M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5m7 7L18 18M6 18l2.5-2.5m7-7L18 6" />,
};

export function Icon({ name, size = 18, className = "" }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

/** A ring showing a 0-1 share, e.g. how much of a search a candidate covers. */
export function Ring({ value, label, size = 56 }) {
  const r = 22, c = 2 * Math.PI * r;
  return (
    <span className="fit-ring" style={{ width: size, height: size }}>
      <svg viewBox="0 0 56 56" width={size} height={size} aria-hidden="true">
        <circle cx="28" cy="28" r={r} className="fit-ring-track" />
        <circle cx="28" cy="28" r={r} className="fit-ring-fill" strokeDasharray={c} strokeDashoffset={c * (1 - value)} />
      </svg>
      <span className="fit-ring-label">{label}</span>
    </span>
  );
}
