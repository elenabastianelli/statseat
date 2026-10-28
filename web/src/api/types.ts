// Names for the contract types. schema.d.ts is generated from app/contract/openapi.json (npm run gen:api):
// never edit it by hand, regenerate it when the contract changes.
import type { components } from "./schema";

type S = components["schemas"];

export type ApiErrorBody = S["ApiError"];
export type Mode = S["Mode"];
export type Phase = S["Phase"];
export type Section = S["Section"];
export type TicketTier = S["TicketTier"];
export type TeamId = S["TeamId"];
export type TeamScore = S["TeamScore"];

export type LoginResponse = S["LoginResponse"];
export type Session = S["Session"];
export type Preferences = S["Preferences"];
export type TicketInfo = S["TicketInfo"];

export type MatchOverview = S["MatchOverview"];
export type TeamSheet = S["TeamSheet"];
export type TeamColours = S["TeamColours"];
export type LineupPlayer = S["LineupPlayer"];
export type MatchState = S["MatchState"];
export type SectionState = S["SectionState"];
export type EventFeed = S["EventFeed"];
export type MatchEvent = S["MatchEvent"];

export type LiveStats = S["LiveStats"];
export type MatchStats = S["MatchStats"];
export type ChartRef = S["ChartRef"];
export type TeamValue = S["TeamValue"];
export type TeamSeries = S["TeamSeries"];
export type TimePoint = S["TimePoint"];
export type Shot = S["Shot"];
export type ZoneMap = S["ZoneMap"];
export type Distance = S["Distance"];
export type StatsInspector = S["StatsInspector"];
export type Recap = S["Recap"];
export type GroundedValue = S["GroundedValue"];

export type HighlightList = S["HighlightList"];
export type Highlight = S["Highlight"];

export type GameList = S["GameList"];
export type GamePrize = S["GamePrize"];
export type Leaderboard = S["Leaderboard"];
export type PickerPlayer = S["PickerPlayer"];
export type PlayerPicker = S["PlayerPicker"];
export type Criterion = S["Criterion"];
export type TttGame = S["TttGame"];
export type TttMoveResult = S["TttMoveResult"];
export type GuessRound = S["GuessRound"];
export type CareerClue = S["CareerClue"];
export type AnswerResult = S["AnswerResult"];
export type QuizRound = S["QuizRound"];

export type ArchieAnswer = S["ArchieAnswer"];
export type InspectorTrace = S["InspectorTrace"];

/** Contract v0.2.1: a metric the backend cannot compute is null and listed in `unavailable` with the reason. */
export type MetricUnavailable = S["MetricUnavailable"];

export function reasonFor(list: MetricUnavailable[] | null | undefined, ...metrics: string[]): string | undefined {
  return list?.find((u) => metrics.includes(u.metric))?.reason;
}
