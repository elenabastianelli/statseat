// Small SVG charts drawn from contract data. Archie never draws: he points to these (ChartRef.chart_id).
import type { ReactNode } from "react";
import type { Shot, TimePoint, ZoneMap } from "../api/types";
import { mirrorGeometry, mirrorPath, shotRadius, shotSymbol } from "../lib/stats";

const PL = 105;
const PW = 68;
/** Contract coordinates (metres, centre spot origin, y up) → SVG (origin top-left, y down). */
const sx = (x: number) => x + PL / 2;
const sy = (y: number) => PW / 2 - y;

export interface Side {
  value: number;
  label: string;
  colour: string;
}

/** Two teams sharing one bar (possession, momentum split). */
export function SplitBar({ home, away }: { home: Side; away: Side }) {
  const total = home.value + away.value || 1;
  const h = (home.value / total) * 100;
  return (
    <div className="split">
      <div className="split-labels">
        <span style={{ color: home.colour }}>{home.label}</span>
        <span style={{ color: away.colour }}>{away.label}</span>
      </div>
      <div className="split-bar" role="img" aria-label={`${home.label} – ${away.label}`}>
        <span style={{ width: `${h}%`, background: home.colour }} />
        <span style={{ width: `${100 - h}%`, background: away.colour }} />
      </div>
    </div>
  );
}

/**
 * One label, the home value on the left and the away value on the right, with bars proportional to each. `homeText` and
 * `awayText` replace the formatted numbers (for the "≈" sign and the reliability symbol).
 */
export function PairRow({ label, home, away, homeColour, awayColour, format = String, homeText, awayText }: {
  label: ReactNode; home: number; away: number; homeColour: string; awayColour: string; format?: (v: number) => string;
  homeText?: ReactNode; awayText?: ReactNode;
}) {
  const max = Math.max(home, away) || 1;
  return (
    <div className="pair">
      <span className="pair-v">{homeText ?? format(home)}</span>
      <div className="pair-mid">
        <span className="pair-label">{label}</span>
        <div className="pair-bars">
          <span className="pair-bar left"><i style={{ width: `${(home / max) * 100}%`, background: homeColour }} /></span>
          <span className="pair-bar"><i style={{ width: `${(away / max) * 100}%`, background: awayColour }} /></span>
        </div>
      </div>
      <span className="pair-v">{awayText ?? format(away)}</span>
    </div>
  );
}

/** Momentum in [-1, 1]: positive = home team pushing. */
export function MomentumGauge({ value, homeColour, awayColour }: { value: number; homeColour: string; awayColour: string }) {
  const w = Math.min(Math.abs(value), 1) * 50;
  return (
    <div className="gauge" role="img" aria-label={`momentum ${value.toFixed(2)}`}>
      <span className="gauge-mid" />
      <span
        className="gauge-fill"
        style={value >= 0
          ? { left: "50%", width: `${w}%`, background: homeColour }
          : { right: "50%", width: `${w}%`, background: awayColour }}
      />
    </div>
  );
}

export interface Line {
  points: TimePoint[];
  colour: string;
  step?: boolean;
}

/** Lines over match minutes. `zero` draws the axis at 0 (momentum). */
export function LineChart({ lines, height = 90, zero = false, yMin, yMax, markers = [] }: {
  lines: Line[]; height?: number; zero?: boolean; yMin?: number; yMax?: number;
  markers?: { minute: number; label: string }[];
}) {
  const all = lines.flatMap((l) => l.points);
  if (all.length === 0) return null;
  const W = 300;
  const x0 = Math.min(...all.map((p) => p.minute));
  const x1 = Math.max(...all.map((p) => p.minute), x0 + 1);
  const lo = yMin ?? Math.min(...all.map((p) => p.value), zero ? 0 : Infinity);
  const hi = yMax ?? Math.max(...all.map((p) => p.value), zero ? 0 : -Infinity);
  const span = hi - lo || 1;
  const X = (m: number) => ((m - x0) / (x1 - x0)) * W;
  const Y = (v: number) => height - 4 - ((v - lo) / span) * (height - 8);
  const path = (l: Line) =>
    l.points
      .map((p, i) => {
        if (i === 0 || !l.step) return `${i ? "L" : "M"}${X(p.minute).toFixed(1)},${Y(p.value).toFixed(1)}`;
        return `H${X(p.minute).toFixed(1)}V${Y(p.value).toFixed(1)}`;
      })
      .join("");
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" role="img">
      {zero && <line x1={0} x2={W} y1={Y(0)} y2={Y(0)} className="axis" />}
      {markers.map((m) => (
        <line key={m.label + m.minute} x1={X(m.minute)} x2={X(m.minute)} y1={0} y2={height} className="marker">
          <title>{m.label}</title>
        </line>
      ))}
      {lines.map((l, i) => (
        <path key={i} d={path(l)} fill="none" stroke={l.colour} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}

export function ChartAxis({ from, to }: { from: number; to: number }) {
  return (
    <div className="axis-labels small muted">
      <span>{from}'</span>
      <span>{to}'</span>
    </div>
  );
}

export function Pitch({ children, label }: { children: ReactNode; label: string }) {
  return (
    <svg className="pitch" viewBox={`-2 -2 ${PL + 4} ${PW + 4}`} role="img" aria-label={label}>
      <rect x={0} y={0} width={PL} height={PW} className="pitch-grass" />
      <g className="pitch-lines">
        <rect x={0} y={0} width={PL} height={PW} />
        <line x1={PL / 2} x2={PL / 2} y1={0} y2={PW} />
        <circle cx={PL / 2} cy={PW / 2} r={9.15} />
        <rect x={0} y={PW / 2 - 20.16} width={16.5} height={40.32} />
        <rect x={PL - 16.5} y={PW / 2 - 20.16} width={16.5} height={40.32} />
        <rect x={0} y={PW / 2 - 9.16} width={5.5} height={18.32} />
        <rect x={PL - 5.5} y={PW / 2 - 9.16} width={5.5} height={18.32} />
      </g>
      {children}
    </svg>
  );
}

/**
 * Shots of both teams on one pitch. Shots are normalised so that the shooting team attacks +x: the home team's
 * are drawn as they are (attacking right), the away team's are mirrored (attacking left). Goals are filled, woodwork is
 * a ×, every other shot is hollow; in Analyst the dot area follows xG (METRICS.md §14).
 */
export function ShotMapChart({ shots, homeId, colourOf, sized }: {
  shots: Shot[]; homeId: string; colourOf: (teamId: string) => string; sized: boolean;
}) {
  return (
    <Pitch label="shot map">
      {shots.map((s) => {
        const mirror = s.team_id !== homeId;
        const x = sx(mirror ? -s.x : s.x);
        const y = sy(mirror ? -s.y : s.y);
        const r = shotRadius(s.xg, sized);
        const c = colourOf(s.team_id);
        const title = <title>{`${s.minute}' ${s.outcome}${s.is_penalty ? " (pen)" : ""}${s.xg != null ? ` · xG ${s.xg.toFixed(2)}` : ""}`}</title>;
        if (shotSymbol(s.outcome) === "cross") {
          const d = Math.max(r, 1.1);
          return (
            <g key={s.event_id} stroke={c} strokeWidth={0.6} strokeLinecap="round">
              <line x1={x - d} y1={y - d} x2={x + d} y2={y + d} />
              <line x1={x - d} y1={y + d} x2={x + d} y2={y - d} />
              {title}
            </g>
          );
        }
        return (
          <circle key={s.event_id} cx={x} cy={y} r={r} fill={shotSymbol(s.outcome) === "filled" ? c : "none"} stroke={c}
            strokeWidth={0.5}>
            {title}
          </circle>
        );
      })}
    </Pitch>
  );
}

/**
 * The one chart over time of Data & Statistics (METRICS.md, "One chart over time"): the home team above the axis, the
 * away team below, on the same scale, so the higher team at each minute is the one further from the axis. Goals are
 * vertical lines in the scorer's colour; `windows` shade a stretch in one team's half (the peak 5 minutes).
 */
export function MirrorChart({ home, away, homeColour, awayColour, goals = [], windows = [], step = false, area = true,
  height = 120, max, to, label, zeroBased = true, floorLabel }: {
  home: TimePoint[]; away: TimePoint[]; homeColour: string; awayColour: string;
  goals?: { minute: number; colour: string }[];
  windows?: { from: number; to: number; above: boolean; colour: string }[];
  step?: boolean; area?: boolean; height?: number; max?: number; to?: number; label: string;
  /** false: the axis is a floor below the lowest value, written under the chart with `floorLabel` */
  zeroBased?: boolean; floorLabel?: (floor: number) => string;
}) {
  if (home.length === 0 && away.length === 0) return null;
  const W = 300;
  const hs = { points: home, above: true };
  const as = { points: away, above: false };
  const g = mirrorGeometry([hs, as], W, height, max, { from: 0, to }, zeroBased);
  return (
    <div className="mirror-wrap">
      <div className="mirror-scale small muted" style={{ height, marginTop: goals.length ? "1.5rem" : "0.4rem" }} aria-hidden>
        <span>{scaleText(g.max)}</span><span>{scaleText(g.floor)}</span><span>{scaleText(g.max)}</span>
      </div>
      <div className="mirror-plot">
      {goals.length > 0 && (
        <div className="mirror-goals small">
          {goals.map((m, i) => (
            <span key={`${m.minute}-${i}`} style={{ left: `${(g.x(m.minute) / W) * 100}%`, color: m.colour }}>⚽ {m.minute}'</span>
          ))}
        </div>
      )}
      <svg className="chart mirror" viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" role="img" aria-label={label}
        style={{ height }}>
        {windows.map((w) => (
          <rect key={`${w.from}-${w.above}`} x={g.x(w.from)} width={Math.max(g.x(w.to) - g.x(w.from), 1)}
            y={w.above ? 0 : g.axisY} height={g.axisY} fill={w.colour} opacity={0.16} />
        ))}
        {area && <path d={mirrorPath(hs, g, step, true)} fill={homeColour} opacity={0.28} />}
        {area && <path d={mirrorPath(as, g, step, true)} fill={awayColour} opacity={0.28} />}
        <path d={mirrorPath(hs, g, step)} fill="none" stroke={homeColour} strokeWidth={2} vectorEffect="non-scaling-stroke" />
        <path d={mirrorPath(as, g, step)} fill="none" stroke={awayColour} strokeWidth={2} vectorEffect="non-scaling-stroke" />
        <line x1={0} x2={W} y1={g.axisY} y2={g.axisY} className="axis" />
        {goals.map((m, i) => (
          <line key={`${m.minute}-${i}`} x1={g.x(m.minute)} x2={g.x(m.minute)} y1={0} y2={height} stroke={m.colour}
            strokeWidth={1.5} strokeDasharray="3 2" vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <ChartAxis from={g.from} to={g.to} />
      {g.floor > 0 && floorLabel && <p className="small muted center">{floorLabel(g.floor)}</p>}
      </div>
    </div>
  );
}

/** A value on the mirror chart's scale: whole numbers from 10 up, one decimal below (xG, momentum). */
const scaleText = (v: number) => (Math.abs(v) >= 10 ? v.toFixed(0) : String(Number(v.toFixed(1))));

/** Share of time per zone (normalised: the team attacks +x). */
export function ZoneGrid({ zone, colour }: { zone: ZoneMap; colour: string }) {
  const max = Math.max(...zone.cells_pct.flat(), 1);
  const cw = PL / zone.cols;
  const ch = PW / zone.rows;
  return (
    <Pitch label="zone map">
      {zone.cells_pct.map((row, r) =>
        row.map((v, c) => (
          <rect key={`${r}-${c}`} x={c * cw} y={r * ch} width={cw} height={ch} fill={colour} opacity={(v / max) * 0.85}>
            <title>{`${v.toFixed(1)}%`}</title>
          </rect>
        )),
      )}
    </Pitch>
  );
}


/** Grouped bars for both teams over the same bins (pass lengths, speed zones). */
export function GroupedBars({ bins, series }: { bins: string[]; series: { values: number[]; colour: string }[] }) {
  const max = Math.max(...series.flatMap((s) => s.values), 1);
  return (
    <div className="bars">
      {bins.map((b, i) => (
        <div className="bars-col" key={b}>
          <div className="bars-stack">
            {series.map((s, j) => (
              <span key={j} style={{ height: `${(s.values[i] / max) * 100}%`, background: s.colour }} title={String(s.values[i])} />
            ))}
          </div>
          <span className="small muted">{b}</span>
        </div>
      ))}
    </div>
  );
}
