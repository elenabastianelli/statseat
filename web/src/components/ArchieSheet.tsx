import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import type { ArchieAnswer } from "../api/types";
import { useMatch } from "../app/MatchContext";
import { useI18n } from "../i18n";
import { lockedPointerNote } from "../lib/phases";
import { TraceView } from "./Inspector";
import { errorText, opensText } from "./Status";
import { Icon } from "./Icon";

/** After this wait the line under the question says why Archie takes his time: he checks every number (AG-5). */
const SLOW_MS = 4000;

interface Message {
  from: "fan" | "archie";
  text: string;
  answer?: ArchieAnswer;
  error?: boolean;
}

/** Archie is reactive only: he answers when asked, never draws, and points to the right chart instead. */
export function ArchieSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const { inspectorOn, openSection, session } = useMatch();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    setSlow(false);
    if (!busy) return;
    const id = window.setTimeout(() => setSlow(true), SLOW_MS);
    return () => window.clearTimeout(id);
  }, [busy]);

  function lockedText(unlocksAt: Parameters<typeof lockedPointerNote>[0]): string {
    const note = lockedPointerNote(unlocksAt, session);
    return note.key === "archie.chart_locked"
      ? t(note.key, { when: opensText(t, note.phase).toLowerCase() })
      : t(note.key);
  }

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  async function send(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    setMessages((m) => [...m, { from: "fan", text }]);
    setBusy(true);
    try {
      const answer = await api.ask(text);
      setMessages((m) => [...m, { from: "archie", text: answer.text, answer }]);
    } catch (err) {
      const e = err instanceof ApiError ? err : new ApiError(0, {});
      setMessages((m) => [...m, { from: "archie", text: errorText(t, e), error: true }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`sheet ${open ? "open" : ""}`} role="dialog" aria-modal="true" aria-label={t("archie.title")} inert={!open}>
      <div className="sheet-backdrop" onClick={onClose} />
      <div className="sheet-panel">
        {/* Archie climbs out of the chat's frame when it opens (L-2h): decorative, the title names the dialog */}
        <img src="/archie-character.svg" alt="" className="archie-pop" />
        <header className="sheet-head archie-head">
          <h2>{t("archie.title")}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><Icon name="close" /></button>
        </header>
        <div className="chat" ref={listRef}>
          <div className="bubble archie">{t("archie.hello")}</div>
          {messages.map((m, i) => (
            <div key={i} className={`bubble ${m.from} ${m.error ? "error" : ""}`}>
              <p>{m.text}</p>
              {m.answer?.points_to && (
                m.answer.points_to.locked ? (
                  <p className="small muted">{lockedText(m.answer.points_to.unlocks_at_phase)}</p>
                ) : (
                  <button className="btn small" onClick={() => {
                    openSection(m.answer!.points_to!.section, m.answer!.points_to!.chart_id);
                    onClose();
                  }}>
                    <Icon name="chart" size={16} /> {t("archie.open_chart")}
                  </button>
                )
              )}
              {inspectorOn && m.answer?.inspector && <TraceView trace={m.answer.inspector} />}
            </div>
          ))}
          {busy && <div className="bubble archie muted">{t(slow ? "archie.thinking_long" : "archie.thinking")}</div>}
        </div>
        <form className="chat-input" onSubmit={send}>
          <input ref={inputRef} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t("archie.placeholder")}
            maxLength={1000} aria-label={t("archie.placeholder")} />
          <button className="btn" disabled={busy || !draft.trim()}>{t("archie.send")}</button>
        </form>
      </div>
    </div>
  );
}
