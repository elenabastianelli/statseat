import { useCallback, useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { api, ApiError } from "../api/client";
import type { MatchState, Mode, Preferences, Section, Session } from "../api/types";
import { Ctx, type MatchCtx } from "../app/MatchContext";
import { APP_NAME, STATE_POLL_MS } from "../config";
import { useI18n } from "../i18n";
import { toggleFavourite as nextFavourites } from "../lib/favourites";
import { canSwitchMode, defaultSection, effectiveMode, isUnlocked, SECONDARY_SECTIONS, TAB_SECTIONS } from "../lib/phases";
import { useLoad } from "../lib/useLoad";
import { DataStats } from "../sections/DataStats";
import { Games } from "../sections/Games";
import { Live } from "../sections/Live";
import { PreMatch } from "../sections/PreMatch";
import { Recap } from "../sections/Recap";
import { ArchieSheet } from "./ArchieSheet";
import { DemoPanel } from "./DemoPanel";
import { Icon, type IconName } from "./Icon";
import { LanguageSwitch } from "./LanguageSwitch";
import { Account } from "./Account";
import { Scoreboard } from "./Scoreboard";
import { ErrorBox, errorText, Loading, opensText } from "./Status";

const TAB_ICON: Partial<Record<Section, IconName>> = {
  pre_match: "preMatch", live: "live", data_stats: "data", games: "games", recap: "recap",
};

// Ask Archie is not a tab: its button is always on screen.
const SECTION_VIEW: Partial<Record<Section, ComponentType>> = {
  pre_match: PreMatch, live: Live, data_stats: DataStats, games: Games, recap: Recap,
};

export function Shell({ session, setSession, onLogout }: {
  session: Session; setSession: (s: Session) => void; onLogout: () => void;
}) {
  const state = useLoad(api.state, [], STATE_POLL_MS);
  const overview = useLoad(api.match, []);
  if (!state.data) return state.error ? <ErrorBox error={state.error} onRetry={state.reload} /> : <Loading />;
  return (
    <Stadium session={session} setSession={setSession} onLogout={onLogout} state={state.data}
      overview={overview.data} reloadState={state.reload} />
  );
}

function Stadium({ session, setSession, onLogout, state, overview, reloadState }: {
  session: Session; setSession: (s: Session) => void; onLogout: () => void; state: MatchState;
  overview: MatchCtx["overview"]; reloadState: () => void;
}) {
  const { t, lang } = useI18n();
  const [section, setSection] = useState<Section>(() => defaultSection(state.phase, state.sections));
  const [focusChart, setFocusChart] = useState<string | null>(null);
  const [inspectorOn, setInspectorOn] = useState(session.inspector_enabled);
  const [archieOpen, setArchieOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const mode = effectiveMode(session, session.preferences.mode);

  // Sections open with the phase: when the phase changes, the app moves to the section of that phase.
  const lastPhase = useRef(state.phase);
  useEffect(() => {
    if (state.phase !== lastPhase.current) {
      lastPhase.current = state.phase;
      setSection(defaultSection(state.phase, state.sections));
    } else if (!isUnlocked(section, state.sections)) {
      setSection(defaultSection(state.phase, state.sections));
    }
  }, [state.phase, state.sections, section]);

  // On a phone the tab row scrolls: keep the open section in view.
  useEffect(() => {
    document.querySelector(".tab.on")?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [section]);

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(id);
  }, [toast]);

  const sessionRef = useRef(session);
  sessionRef.current = session;
  const updatePrefs = useCallback(async (patch: Partial<Preferences>) => {
    const before = sessionRef.current;
    const next = { ...before.preferences, ...patch };
    setSession({ ...before, preferences: next });
    try {
      const saved = await api.putPreferences(next);
      setSession({ ...sessionRef.current, preferences: saved });
    } catch (e) {
      setSession(before);
      setToast(errorText(t, e instanceof ApiError ? e : new ApiError(0, {})));
    }
  }, [setSession, t]);

  // The UI language is also Archie's default language.
  useEffect(() => {
    if (session.preferences.language !== lang) updatePrefs({ language: lang });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang]);

  const toggleFavourite = useCallback((playerId: string) => {
    const r = nextFavourites(sessionRef.current.preferences, playerId);
    if ("refused" in r) setToast(t(r.refused === "locked" ? "account.fav_locked_toast" : "account.fav_full_toast"));
    else updatePrefs({ favourite_player_ids: r.favourites });
  }, [updatePrefs, t]);

  const openSection = useCallback((s: Section, chartId?: string) => {
    if (!isUnlocked(s, state.sections)) {
      setToast(opensText(t, state.sections.find((x) => x.section === s)?.unlocks_at_phase));
      return;
    }
    setSection(s);
    setFocusChart(chartId ?? null);
  }, [state.sections, t]);

  const ctx = useMemo<MatchCtx>(
    () => ({ session, state, overview, mode, inspectorOn, focusChart, openSection, toggleFavourite, updatePrefs }),
    [session, state, overview, mode, inspectorOn, focusChart, openSection, toggleFavourite, updatePrefs],
  );
  const View = SECTION_VIEW[section] ?? PreMatch;

  return (
    <Ctx.Provider value={ctx}>
      <div className="app">
        <header className="top">
          <div className="topbar">
            <span className="brand"><img src="/icon-512.png" alt="" /> {APP_NAME}</span>
            <span className={`badge tier-${session.tier}`}>{t(`tier.${session.tier}`)}</span>
            <span className="grow" />
            <LanguageSwitch compact />
            <button className="icon-btn" onClick={() => setAccountOpen(true)} aria-label={t("nav.account")} title={t("nav.account")}><Icon name="person" size={22} /></button>
            <button className="icon-btn" onClick={onLogout} aria-label={t("nav.logout")} title={t("nav.logout")}><Icon name="signOut" size={22} /></button>
          </div>
          <Scoreboard />
          {(canSwitchMode(session) || session.inspector_enabled) && (
            <div className="toolbar">
              {canSwitchMode(session) && (
                <div className="seg" role="group">
                  {session.allowed_modes.map((m: Mode) => (
                    <button key={m} className={m === mode ? "on" : ""} aria-pressed={m === mode} onClick={() => updatePrefs({ mode: m })}>
                      {t(`mode.${m}`)}
                    </button>
                  ))}
                </div>
              )}
              {session.inspector_enabled && (
                <label className="switch">
                  <input type="checkbox" checked={inspectorOn} onChange={(e) => setInspectorOn(e.target.checked)} />
                  <span>{t("nav.inspector")}</span>
                </label>
              )}
            </div>
          )}
          <nav className="tabs" aria-label="Sections">
            {TAB_SECTIONS.map((s) => {
              const st = state.sections.find((x) => x.section === s);
              const open = st?.unlocked ?? false;
              return (
                <button key={s} className={`tab ${s === section ? "on" : ""} ${open ? "" : "locked"} ${SECONDARY_SECTIONS.includes(s) ? "secondary" : ""}`}
                  aria-current={s === section ? "page" : undefined} onClick={() => openSection(s)}
                  title={open ? undefined : opensText(t, st?.unlocks_at_phase)}>
                  <Icon name={open ? TAB_ICON[s] ?? "dot" : "lock"} size={18} />
                  {t(`section.${s}`)}
                </button>
              );
            })}
          </nav>
        </header>

        {session.demo_panel_enabled && <DemoPanel phase={state.phase} onJump={reloadState} />}

        <main className="content" key={`${section}-${mode}`}>
          <View />
        </main>

        <button className="archie-fab" onClick={() => setArchieOpen(true)} aria-label={t("section.ask_archie")}>
          <img src="/archie.svg" alt="" />
          <span>{t("section.ask_archie")}</span>
        </button>
        <ArchieSheet open={archieOpen} onClose={() => setArchieOpen(false)} />
        {accountOpen && <Account onClose={() => setAccountOpen(false)} />}
        {toast && <div className="toast" role="status">{toast}</div>}
      </div>
    </Ctx.Provider>
  );
}
