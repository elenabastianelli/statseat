import { useState } from "react";
import { api } from "../api/client";
import type { Phase } from "../api/types";
import { useI18n } from "../i18n";

const PHASES: Phase[] = ["pre_match", "first_half", "half_time", "second_half", "full_time"];

/** Demo tickets only (session.demo_panel_enabled): jump between phases to show how the app changes. */
export function DemoPanel({ phase, onJump }: { phase: Phase; onJump: () => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  async function jump(p: Phase) {
    setBusy(true);
    try {
      await api.demoPhase(p);
      onJump();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="demo" aria-label={t("demo.title")}>
      <span className="demo-label">{t("demo.title")}</span>
      <div className="demo-btns" role="group" aria-label={t("demo.hint")}>
        {PHASES.map((p) => (
          <button key={p} className={`chip ${p === phase ? "on" : ""}`} disabled={busy} onClick={() => jump(p)}>
            {t(`phase.${p}`)}
          </button>
        ))}
      </div>
    </div>
  );
}
