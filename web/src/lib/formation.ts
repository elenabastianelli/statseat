/**
 * Where each English position code sits in its team's half, for the line-up pitch (L-2e).
 * `side`: -1 left … +1 right, as the team attacks; `depth`: 0 own goal line … 1 halfway line.
 */
export const POSITION_SPOT: Record<string, { side: number; depth: number }> = {
  GK: { side: 0, depth: 0.06 },
  LCB: { side: -0.3, depth: 0.25 }, CB: { side: 0, depth: 0.25 }, RCB: { side: 0.3, depth: 0.25 },
  LB: { side: -0.78, depth: 0.33 }, RB: { side: 0.78, depth: 0.33 },
  LWB: { side: -0.72, depth: 0.46 }, RWB: { side: 0.72, depth: 0.46 },
  LDM: { side: -0.3, depth: 0.47 }, CDM: { side: 0, depth: 0.47 }, RDM: { side: 0.3, depth: 0.47 },
  LCM: { side: -0.3, depth: 0.6 }, CM: { side: 0, depth: 0.6 }, RCM: { side: 0.3, depth: 0.6 },
  LM: { side: -0.8, depth: 0.64 }, RM: { side: 0.8, depth: 0.64 },
  CAM: { side: 0, depth: 0.72 }, LW: { side: -0.74, depth: 0.8 }, RW: { side: 0.74, depth: 0.8 },
  LS: { side: -0.3, depth: 0.95 }, ST: { side: 0, depth: 0.95 }, RS: { side: 0.3, depth: 0.95 },
};

export const PITCH_W = 68;
export const PITCH_L = 105;

/**
 * Point on a vertical pitch (viewBox 68 × 105): the home team defends the bottom goal and attacks up, the away
 * team the other way, so "right" is mirrored for the away team. Unknown codes go to the centre of the half.
 */
export function spotOnPitch(position: string, role: "home" | "away"): { x: number; y: number } {
  const p = POSITION_SPOT[position] ?? { side: 0, depth: 0.5 };
  const fromGoal = 3 + p.depth * 47; // metres from the team's own goal line, kept clear of the halfway line
  const lateral = p.side * 28;
  return role === "home"
    ? { x: PITCH_W / 2 + lateral, y: PITCH_L - fromGoal }
    : { x: PITCH_W / 2 - lateral, y: fromGoal };
}
