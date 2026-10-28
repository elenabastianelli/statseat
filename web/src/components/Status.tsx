import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import type { Phase } from "../api/types";
import { useI18n } from "../i18n";
import { Icon } from "./Icon";

export function errorText(t: ReturnType<typeof useI18n>["t"], e: ApiError): string {
  const key = `err.${e.code}`;
  const s = t(key);
  return s === key ? t("err.generic") : s;
}

export function Loading() {
  const { t } = useI18n();
  return <p className="muted center pad">{t("common.loading")}</p>;
}

export function opensText(t: ReturnType<typeof useI18n>["t"], phase: Phase | null | undefined): string {
  return t(phase ? `opens.${phase}` : "opens.never");
}

export function Locked({ phase }: { phase: Phase | null | undefined }) {
  const { t } = useI18n();
  return (
    <div className="empty">
      <Icon name="lock" size={32} className="empty-icon" />
      <p>{opensText(t, phase)}</p>
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const { t } = useI18n();
  if (error.status === 423) return <Locked phase={error.unlocksAtPhase} />;
  return (
    <div className="empty" role="alert">
      <p>{errorText(t, error)}</p>
      {onRetry && <button className="btn ghost" onClick={onRetry}>{t("common.retry")}</button>}
    </div>
  );
}

/** Renders `children` with the loaded data, or the loading / error state. */
export function Load<T>({ data, error, reload, children }: {
  data: T | null; error: ApiError | null; reload: () => void; children: (d: T) => ReactNode;
}) {
  if (data) return <>{children(data)}</>;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return <Loading />;
}

export function Card({ title, id, children, aside }: { title?: string; id?: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="card" id={id}>
      {(title || aside) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

/** A metric the backend could not compute (contract v0.2.1): the reason, never a number. */
export function Unavailable({ reason }: { reason?: string }) {
  const { t } = useI18n();
  return (
    <p className="muted small">
      {t("stats.unavailable")}{reason ? `: ${reason}` : ""}
    </p>
  );
}
