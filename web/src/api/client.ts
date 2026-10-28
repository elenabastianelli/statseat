import { API_BASE } from "../config";
import { storage } from "../lib/storage";
import type {
  AnswerResult, ApiErrorBody, ArchieAnswer, EventFeed, GameList, GuessRound, HighlightList, Leaderboard, LiveStats,
  LoginResponse, MatchOverview, MatchState, MatchStats, Mode, Phase, PlayerPicker, Preferences, QuizRound, Recap, Session, TicketInfo,
  TttGame, TttMoveResult,
} from "./types";

const TOKEN_KEY = "statseat.token";
const DEVICE_KEY = "statseat.device";

/** An error answered by the backend (ApiError of the contract), or a network failure (status 0). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly section: string | null;
  readonly unlocksAtPhase: Phase | null;

  constructor(status: number, body: Partial<ApiErrorBody>) {
    super(body.message ?? `HTTP ${status}`);
    this.status = status;
    this.code = body.code ?? (status === 0 ? "network_error" : "http_error");
    this.section = body.section ?? null;
    this.unlocksAtPhase = (body.unlocks_at_phase as Phase | null | undefined) ?? null;
  }
}

/** FastAPI validation errors (422) have `detail` instead of the contract's ApiError fields. */
export function toApiError(status: number, body: unknown): ApiError {
  if (body && typeof body === "object" && "code" in body) return new ApiError(status, body as ApiErrorBody);
  if (status === 422) return new ApiError(status, { code: "validation_error", message: "Invalid request." });
  return new ApiError(status, {});
}

/**
 * Random id created once and kept on this device: silver and gold tickets are bound to it.
 * crypto.getRandomValues also works over plain http on the local network (randomUUID does not).
 */
export function deviceId(): string {
  let id = storage.get(DEVICE_KEY);
  if (!id) {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    id = "dev-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    storage.set(DEVICE_KEY, id);
  }
  return id;
}

let token: string | null = storage.get(TOKEN_KEY);
let onUnauthorized: () => void = () => {};

export const auth = {
  hasToken: () => token !== null,
  logout() {
    token = null;
    storage.remove(TOKEN_KEY);
  },
  /** Called when the backend no longer knows the token (e.g. the mock server restarted). */
  onUnauthorized(fn: () => void) {
    onUnauthorized = fn;
  },
};

async function call(method: string, path: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, { message: "The server cannot be reached." });
  }
  if (!res.ok) {
    const err = toApiError(res.status, await res.json().catch(() => null));
    if (err.status === 401 && err.code === "invalid_token") {
      auth.logout();
      onUnauthorized();
    }
    throw err;
  }
  return res;
}

const json = async <T>(method: string, path: string, body?: unknown): Promise<T> =>
  (await call(method, path, body)).json() as Promise<T>;

const q = (mode?: Mode) => (mode ? `?mode=${mode}` : "");

export const api = {
  async login(ticketCode: string): Promise<Session> {
    const res = await json<LoginResponse>("POST", "/api/auth/login", { ticket_code: ticketCode, device_id: deviceId() });
    token = res.token;
    storage.set(TOKEN_KEY, res.token);
    return res.session;
  },
  session: () => json<Session>("GET", "/api/session"),
  putPreferences: (p: Preferences) => json<Preferences>("PUT", "/api/session/preferences", p),
  ticket: () => json<TicketInfo>("GET", "/api/ticket"),
  ticketImage: async (format: "svg" | "pdf" | "png") => (await call("GET", `/api/ticket/image?format=${format}`)).blob(),
  match: () => json<MatchOverview>("GET", "/api/match"),
  state: () => json<MatchState>("GET", "/api/match/state"),
  events: () => json<EventFeed>("GET", "/api/match/events"),
  liveStats: () => json<LiveStats>("GET", "/api/stats/live"),
  stats: (mode?: Mode) => json<MatchStats>("GET", `/api/stats${q(mode)}`),
  recap: (mode?: Mode) => json<Recap>("GET", `/api/recap${q(mode)}`),
  /** The final report, built by the server (L-2g): a PDF file to download. */
  recapPdf: async (mode?: Mode) => (await call("GET", `/api/recap/pdf${q(mode)}`)).blob(),
  highlights: () => json<HighlightList>("GET", "/api/highlights"),
  games: () => json<GameList>("GET", "/api/games"),
  leaderboard: () => json<Leaderboard>("GET", "/api/leaderboard"),
  gamePlayers: () => json<PlayerPicker>("GET", "/api/games/players"),
  tttStart: () => json<TttGame>("POST", "/api/games/tiki-taka-toe"),
  tttGet: (id: string) => json<TttGame>("GET", `/api/games/tiki-taka-toe/${encodeURIComponent(id)}`),
  tttMove: (id: string, row: number, col: number, playerId: string) =>
    json<TttMoveResult>("POST", `/api/games/tiki-taka-toe/${encodeURIComponent(id)}/moves`, { row, col, player_id: playerId }),
  guessStart: () => json<GuessRound>("POST", "/api/games/guess-the-player"),
  guessAnswer: (roundId: string, playerId: string) =>
    json<AnswerResult>("POST", `/api/games/guess-the-player/${encodeURIComponent(roundId)}/answer`, { option_id: playerId }),
  quizStart: () => json<QuizRound>("POST", "/api/games/quiz"),
  quizAnswer: (roundId: string, optionId: string) =>
    json<AnswerResult>("POST", `/api/games/quiz/${encodeURIComponent(roundId)}/answer`, { option_id: optionId }),
  /** A club's crest (M-1): a public image, so a plain <img src>. */
  crestUrl: (clubId: string) => `${API_BASE}/api/crests/${encodeURIComponent(clubId)}`,
  ask: (message: string, language?: string) => json<ArchieAnswer>("POST", "/api/archie/ask", { message, language }),
  demoPhase: (phase: Phase) => json<MatchState>("POST", "/api/demo/phase", { phase }),
};
