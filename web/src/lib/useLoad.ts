import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export interface Loaded<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Loads `fn` when `deps` change and, with `pollMs`, again every `pollMs` (keeping the last data on screen). */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], pollMs?: number): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let alive = true;
    const run = () =>
      fnRef.current().then(
        (d) => alive && (setData(d), setError(null), setLoading(false)),
        (e: unknown) =>
          alive && (setError(e instanceof ApiError ? e : new ApiError(0, {})), setLoading(false)),
      );
    setLoading(true);
    run();
    const id = pollMs ? window.setInterval(run, pollMs) : undefined;
    return () => {
      alive = false;
      if (id) window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, pollMs]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload };
}
