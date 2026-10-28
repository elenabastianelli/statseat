import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { LineupPlayer, TeamSheet, TicketInfo } from "../api/types";
import { player, teamOfPlayer, useMatch } from "../app/MatchContext";
import { useI18n } from "../i18n";
import { canConfirmFavourites, MAX_FAVOURITES } from "../lib/favourites";
import { date } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { Card, errorText, Load, opensText } from "./Status";
import { Icon } from "./Icon";
import { LanguageSwitch } from "./LanguageSwitch";

/**
 * Account (L-2g): My ticket, the language, the 3 favourite players (locked once confirmed, contract v0.2.6) and the
 * final report in PDF, built by the server after the final whistle.
 */
export function Account({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const ticket = useLoad(api.ticket, []);
  return (
    <div className="page-overlay" role="dialog" aria-modal="true" aria-label={t("account.title")}>
      <header className="sheet-head">
        <h2>{t("account.title")}</h2>
        <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><Icon name="close" /></button>
      </header>
      <div className="page-body account">
        <Card title={t("ticket.title")}>
          <Load {...ticket}>{(tk) => <TicketView tk={tk} />}</Load>
        </Card>
        <Card title={t("account.language")}>
          <LanguageSwitch />
          <p className="small muted">{t("account.language_hint")}</p>
        </Card>
        <Favourites />
        <Report />
      </div>
    </div>
  );
}

/** The 3 favourite players: chosen here or with the stars of Pre-match, then confirmed once and for all. */
function Favourites() {
  const { t } = useI18n();
  const { session, overview, toggleFavourite, updatePrefs } = useMatch();
  const prefs = session.preferences;
  const favs = prefs.favourite_player_ids ?? [];
  const [asking, setAsking] = useState(false);
  const slots = Array.from({ length: MAX_FAVOURITES }, (_, i) => favs[i]);

  return (
    <Card title={t("account.favourites")} aside={prefs.favourites_locked ? <Icon name="lockFilled" size={18} /> : undefined}>
      {prefs.favourites_locked
        ? <p className="small muted">{t("account.fav_locked")}</p>
        : <p className="warn small"><Icon name="warning" size={18} /> {t("account.fav_warning", { n: MAX_FAVOURITES })}</p>}

      <ol className="fav-slots">
        {slots.map((id, i) => {
          const p = player(overview, id);
          return (
            <li key={i} className={p ? "" : "empty"}>
              {p ? (
                <>
                  <Shirt p={p} team={overview?.teams.find((tm) => tm.team_id === teamOfPlayer(overview, p.player_id))} />
                  <span className="grow">{p.name}</span>
                  {!prefs.favourites_locked && (
                    <button className="icon-btn" onClick={() => toggleFavourite(p.player_id)} aria-label={t("pre.unfollow", { name: p.name })}>
                      <Icon name="close" size={18} />
                    </button>
                  )}
                </>
              ) : <span className="muted">{t("account.fav_empty", { n: i + 1 })}</span>}
            </li>
          );
        })}
      </ol>

      {!prefs.favourites_locked && (
        <>
          {overview?.teams.map((tm) => (
            <details key={tm.team_id} className="fav-team">
              <summary>{tm.name}</summary>
              <ul className="lineup">
                {tm.lineup.map((p) => {
                  const on = favs.includes(p.player_id);
                  return (
                    <li key={p.player_id}>
                      <Shirt p={p} team={tm} />
                      <span className="grow">{p.name}</span>
                      <span className="muted small">{p.position}</span>
                      <button className={`star ${on ? "on" : ""}`} aria-pressed={on} onClick={() => toggleFavourite(p.player_id)}
                        aria-label={t(on ? "pre.unfollow" : "pre.follow", { name: p.name })}>
                        <Icon name={on ? "starFilled" : "star"} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </details>
          ))}
          {asking ? (
            <div className="confirm-box" role="alertdialog" aria-label={t("account.fav_confirm")}>
              <p><strong>{t("account.fav_confirm_question")}</strong></p>
              <div className="row wrap">
                <button className="btn" onClick={() => updatePrefs({ favourites_locked: true }).then(() => setAsking(false))}>
                  {t("account.fav_confirm_yes")}
                </button>
                <button className="btn ghost" onClick={() => setAsking(false)}>{t("account.fav_confirm_no")}</button>
              </div>
            </div>
          ) : (
            <button className="btn big" disabled={!canConfirmFavourites(prefs)} onClick={() => setAsking(true)}>
              {t("account.fav_confirm")}
            </button>
          )}
        </>
      )}
    </Card>
  );
}

function Shirt({ p, team }: { p: LineupPlayer; team?: TeamSheet }) {
  return <span className="shirt" style={{ background: team?.colours.primary, color: team?.colours.number }}>{p.shirt_number}</span>;
}

/** The final report: the server builds the PDF from the Recap, so it opens with the Recap (after the final whistle). */
function Report() {
  const { t } = useI18n();
  const { state, mode } = useMatch();
  const recap = state.sections.find((s) => s.section === "recap");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      saveBlob(await api.recapPdf(mode), `statseat-report-${state.match_id}.pdf`);
    } catch (e) {
      setError(errorText(t, e instanceof ApiError ? e : new ApiError(0, {})));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t("account.report")}>
      <p className="small muted">{t("account.report_hint")}</p>
      {recap?.unlocked ? (
        <button className="btn big" onClick={download} disabled={busy}>
          <Icon name={busy ? "download" : "pdf"} size={20} /> {t(busy ? "account.report_busy" : "account.report_download")}
        </button>
      ) : <p className="muted"><Icon name="lock" size={18} /> {opensText(t, recap?.unlocks_at_phase)}</p>}
      {error && <p className="bad small">{error}</p>}
    </Card>
  );
}

function saveBlob(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function TicketView({ tk }: { tk: TicketInfo }) {
  const { t, lang } = useI18n();
  const hasSvg = tk.images.some((i) => i.format === "svg" && i.available);
  const [imgUrl, setImgUrl] = useState<string | null>(null);

  // The image needs the bearer token, so it is fetched and shown from a blob URL.
  useEffect(() => {
    if (!hasSvg) return;
    let url: string | null = null;
    api.ticketImage("svg").then((b) => setImgUrl((url = URL.createObjectURL(b))), () => setImgUrl(null));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [hasSvg]);

  async function download(format: "pdf" | "png" | "svg") {
    saveBlob(await api.ticketImage(format), `${tk.ticket_id}.${format}`);
  }

  return (
    <>
      {imgUrl ? <img className="ticket-img" src={imgUrl} alt={`${tk.ticket_id} ${t(`tier.${tk.tier}`)}`} /> : <Placeholder tk={tk} />}

      <div className="row pad-y">
        <span className={`badge tier-${tk.tier}`}>{t(`tier.${tk.tier}`)}</span>
        <span className={tk.valid_now ? "ok" : "bad"}>{t(tk.valid_now ? "ticket.valid_now" : "ticket.not_valid_now")}</span>
      </div>
      <p className="small muted">{t("ticket.valid", { from: date(tk.valid_from, lang), until: date(tk.valid_until, lang) })}</p>

      <h3>{t("ticket.unlocks")}</h3>
      <ul>{tk.features.perks.map((p) => <li key={p}>{p}</li>)}</ul>

      <div className="row wrap">
        {tk.images.filter((i) => i.available).map((i) => (
          <button key={i.format} className="btn ghost small" onClick={() => download(i.format)}>
            {t("ticket.download", { fmt: i.format.toUpperCase() })}
          </button>
        ))}
      </div>
    </>
  );
}

/** Drawn from the ticket data while the printed image does not exist yet. No personal data, never the code. */
function Placeholder({ tk }: { tk: TicketInfo }) {
  const { t, lang } = useI18n();
  const s = tk.seat;
  return (
    <div className={`ticket tier-${tk.tier}`}>
      <div className="ticket-top">
        <span className="ticket-brand">statseat</span>
        <span className="ticket-tier">{t(`tier.${tk.tier}`)}</span>
      </div>
      <p className="ticket-title">{tk.match.home_name} – {tk.match.away_name}</p>
      <p className="small">{tk.match.title} · {tk.match.stadium_name} · {date(tk.match.kickoff, lang)}</p>
      {s ? (
        <dl className="ticket-seat">
          <div><dt>{t("ticket.stand")}</dt><dd>{s.stand_name}</dd></div>
          <div><dt>{t("ticket.sector")}</dt><dd>{s.sector_id}</dd></div>
          <div><dt>{t("ticket.row")}</dt><dd>{s.row}</dd></div>
          <div><dt>{t("ticket.seat")}</dt><dd>{s.seat}</dd></div>
          {s.gate_id && <div><dt>{t("ticket.gate")}</dt><dd>{s.gate_id}</dd></div>}
        </dl>
      ) : <p className="ticket-seat">{t("ticket.pass")}</p>}
      <p className="ticket-id mono">{tk.ticket_id}</p>
      <p className="small ticket-note">{t("ticket.image_pending")}</p>
    </div>
  );
}
