"use client";

import { useEffect, useState } from "react";
import { HEALTH_TIMEOUT_MS, parseHealth, RECONNECTING_AFTER, retryDelayMs, type BackendHealth } from "@/lib/backendHealth";

export interface BackendHealthState<T extends BackendHealth> {
  /** The backend's answer; undefined until one arrives. */
  health: T | undefined;
  /** The backend hasn't answered for a few tries: show "Reconnecting". */
  reconnecting: boolean;
}

// GET /health until the backend gives a real answer. While it can't be
// reached (a restart, a deploy) `health` stays undefined, which every first
// run rule reads as "not known yet", and it asks again with a growing wait,
// and at once when the browser comes back online or the tab is shown again.
// Pass enabled=false to skip the call.
export function useBackendHealth<T extends BackendHealth = BackendHealth>(enabled = true): BackendHealthState<T> {
  const [health, setHealth] = useState<T | undefined>(undefined);
  const [failures, setFailures] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let misses = 0;
    let inFlight = false;

    const ask = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      clearTimeout(timer);
      let answer: BackendHealth | null = null;
      try {
        // A hang (a deploy that took the connection but isn't serving yet)
        // counts as a miss, so the strip and the retries still happen.
        const r = await fetch("/api/backend/health", { signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS) });
        const body: unknown = await r.json().catch(() => null);
        answer = parseHealth(r.ok, body);
      } catch {
        answer = null;
      }
      inFlight = false;
      if (cancelled) return;
      if (answer) {
        misses = 0;
        setFailures(0);
        setHealth(answer as T);
        stop();
        return;
      }
      misses += 1;
      setFailures(misses);
      timer = setTimeout(() => void ask(), retryDelayMs(misses));
    };
    const askNow = () => {
      if (document.visibilityState === "visible") void ask();
    };
    const stop = () => {
      window.removeEventListener("online", askNow);
      document.removeEventListener("visibilitychange", askNow);
    };

    window.addEventListener("online", askNow);
    document.addEventListener("visibilitychange", askNow);
    void ask();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      stop();
      // Forget this run's answer: switched off and back on (away to setup
      // and back), the page must wait for a fresh one, not act on the old.
      setHealth(undefined);
      setFailures(0);
    };
  }, [enabled]);

  return { health, reconnecting: health === undefined && failures >= RECONNECTING_AFTER };
}
