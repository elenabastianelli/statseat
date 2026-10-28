import type { Highlight } from "../api/types";
import { teamColour, useMatch } from "../app/MatchContext";
import { Icon, type IconName } from "./Icon";

const ICON: Record<string, IconName> = {
  goal: "goal", woodwork: "woodwork", save: "save", big_chance: "chance", var_review: "var", card: "card",
  spectacular_play: "sparkle", speed_peak: "speed", crowd_peak: "crowd", other: "dot",
};

/** Highlights chosen by VAR, in match order (the 2D replay is a roadmap item since 25 October). */
export function Timeline({ highlights }: { highlights: Highlight[] }) {
  const { overview, inspectorOn } = useMatch();
  const sorted = [...highlights].sort((a, b) => a.period - b.period || a.minute - b.minute);
  return (
    <ol className="timeline">
      {sorted.map((h) => (
        <li key={h.highlight_id} style={{ borderColor: teamColour(overview, h.team_id) }}>
          <div className="tl-head">
            <Icon name={ICON[h.type] ?? "dot"} className="tl-icon" />
            <strong>{h.label}</strong>
          </div>
          <p className="small muted">{h.why_it_mattered}</p>
          {inspectorOn && h.inspector && (
            <p className="small mono muted">
              importance {h.importance.toFixed(0)} · rank {h.inspector.candidate_rank} · {h.inspector.labelled_by} ({h.inspector.model}) ·{" "}
              {Object.entries(h.inspector.importance_factors).map(([k, v]) => `${k} ${v}`).join(", ")}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}
