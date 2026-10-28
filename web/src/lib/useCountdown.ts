import { useEffect, useMemo, useState } from "react";

/** Whole seconds left of a `seconds`-long timer that starts when `key` changes (a new turn or round), re-rendered 4 times
 * a second; 0 when past or when `key` is null. Measured on this device's monotonic clock from the moment the server's
 * answer arrives, so a phone whose clock is off still shows the server's 20 s (comparing the server's absolute deadline
 * with the phone's clock did not). The server starts its timer first, so the app never runs out before the server does. */
export function useCountdown(seconds: number | null | undefined, key: string | null | undefined): number {
  const [, setTick] = useState(0);
  const started = useMemo(() => performance.now(), [key]); // a new turn or round: start again
  useEffect(() => {
    if (!key) return;
    const id = window.setInterval(() => setTick((n) => n + 1), 250);
    return () => window.clearInterval(id);
  }, [key]);
  if (!key || seconds == null) return 0;
  return Math.max(0, Math.ceil(seconds - (performance.now() - started) / 1000));
}
