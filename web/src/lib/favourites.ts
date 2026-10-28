import type { Preferences } from "../api/types";

/** At most 3 favourite players; once confirmed they can no longer change (contract v0.2.6, the server checks too). */
export const MAX_FAVOURITES = 3;

export type ToggleResult = { favourites: string[] } | { refused: "locked" | "full" };

/** The favourites after tapping a player's star, or why the tap is refused. */
export function toggleFavourite(prefs: Pick<Preferences, "favourite_player_ids" | "favourites_locked">, playerId: string): ToggleResult {
  const favs = prefs.favourite_player_ids ?? [];
  if (prefs.favourites_locked) return { refused: "locked" };
  if (favs.includes(playerId)) return { favourites: favs.filter((p) => p !== playerId) };
  if (favs.length >= MAX_FAVOURITES) return { refused: "full" };
  return { favourites: [...favs, playerId] };
}

export function canConfirmFavourites(prefs: Pick<Preferences, "favourite_player_ids" | "favourites_locked">): boolean {
  return !prefs.favourites_locked && (prefs.favourite_player_ids ?? []).length === MAX_FAVOURITES;
}
