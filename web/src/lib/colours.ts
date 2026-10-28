import type { TeamColours } from "../api/types";

/** App background (keep equal to --bg in index.css): team marks must stand out against it. */
export const BG = "#12081F";

function luminance(hex: string): number {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return 0;
  const n = parseInt(m[1], 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function lab(hex: string): [number, number, number] {
  const n = parseInt(hex.trim().replace("#", ""), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  const x = f((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047);
  const y = f(0.2126 * r + 0.7152 * g + 0.0722 * b);
  const z = f((0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883);
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)];
}

/** Perceptual distance between two colours (CIE76 ΔE): below about 30 two team marks are easy to confuse. */
export function colourDistance(a: string, b: string): number {
  const [p, q] = [lab(a), lab(b)];
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
}

/** Minimum ΔE between the two teams' marks. */
export const MIN_TEAM_DISTANCE = 30;

/** The colours a team can be shown with, in order of preference: home shirt, home trim, then the second kit. */
function candidates(c: TeamColours): string[] {
  const kit = c.away_kit;
  return [c.primary, c.secondary, ...(kit ? [kit.shirt, kit.socks, kit.shorts] : [])];
}

/**
 * The club colour used for charts and markers: the first colour of the club that reads on the dark background
 * (contrast 3:1), from the home kit first (Brackwater navy → amber, Calderholm maroon → sky blue).
 */
export function teamMark(c: TeamColours): string {
  return candidates(c).find((x) => contrast(x, BG) >= 3) ?? c.secondary;
}

/**
 * The marks of the two teams together: the home team keeps its mark; if the away team's is too similar
 * (ΔE < MIN_TEAM_DISTANCE), it switches to the first readable colour of its second kit that is different enough.
 */
export function teamMarks(home: TeamColours, away: TeamColours): { home: string; away: string } {
  const h = teamMark(home);
  const readable = candidates(away).filter((x) => contrast(x, BG) >= 3);
  const a = readable.find((x) => colourDistance(x, h) >= MIN_TEAM_DISTANCE) ?? teamMark(away);
  return { home: h, away: a };
}
