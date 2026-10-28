// Inspector mode: method, agents, tools, tokens and trace. Only demo tickets get these fields (the server
// sends null to silver and gold); the app also hides them when the viewer turns Inspector off.
import type { InspectorTrace, StatsInspector } from "../api/types";
import { useI18n } from "../i18n";

export function TraceView({ trace }: { trace: InspectorTrace }) {
  const { t } = useI18n();
  const g = trace.grounding;
  return (
    <details className="inspector">
      <summary>
        <span className="badge insp">{t("inspector.title")}</span>{" "}
        <span className={g.passed ? "ok" : "bad"}>
          {t("inspector.grounding")}: {t(g.passed ? "inspector.passed" : "inspector.failed")}
        </span>
      </summary>
      <p className="small">{trace.method}</p>
      <h4>{t("inspector.agents")}</h4>
      <ul className="small mono">
        {trace.agents.map((a, i) => (
          <li key={i}>{a.agent} · {a.model} · {a.role} · {a.tokens_in}/{a.tokens_out} tok · {a.latency_ms} ms</li>
        ))}
      </ul>
      <h4>{t("inspector.tools")}</h4>
      <ul className="small mono">
        {trace.tool_calls.map((c, i) => (
          <li key={i}>
            {c.agent} → {c.tool}({Object.entries(c.arguments ?? {}).map(([k, v]) => `${k}=${v}`).join(", ")}) · {c.latency_ms} ms
            <br />
            <span className="muted">{c.result_summary}</span>
          </li>
        ))}
      </ul>
      <p className="small">
        {t("inspector.checked", { n: g.numbers_checked })}
        {g.numbers_unmatched && g.numbers_unmatched.length > 0 && <span className="bad"> · {g.numbers_unmatched.join(", ")}</span>}
      </p>
      {trace.evidence && trace.evidence.length > 0 && (
        <p className="small">{t("inspector.evidence")}: <span className="mono">{trace.evidence.join(", ")}</span></p>
      )}
      <p className="small mono muted">
        {t("inspector.totals", { tokens: trace.total_tokens, ms: trace.latency_ms, cost: trace.cost_usd.toFixed(4) })} ·{" "}
        {t("inspector.trace", { id: trace.trace_id })}
      </p>
    </details>
  );
}

export function MethodsView({ inspector }: { inspector: StatsInspector }) {
  const { t } = useI18n();
  return (
    <details className="inspector">
      <summary>
        <span className="badge insp">{t("inspector.title")}</span> {t("stats.method")}
      </summary>
      <p className="small muted">{t("stats.source", { source: inspector.source })}</p>
      <ul className="small">
        {inspector.methods.map((m) => (
          <li key={m.metric}>
            <strong>{m.metric}</strong>: {m.method}
            <br />
            <span className="mono muted">
              {m.tool}({Object.entries(m.parameters ?? {}).map(([k, v]) => `${k}=${v}`).join(", ")}) ·{" "}
              {t("stats.excluded", { n: m.data_excluded_pct })}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}
