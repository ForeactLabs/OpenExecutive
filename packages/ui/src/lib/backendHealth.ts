// What a page may conclude from GET /health. Only a real answer from the
// backend says whether a company profile exists: a failed request, a proxy's
// error page or an error body (the backend restarting, a deploy, a network
// blip) says nothing about setup, so it must never read as "not set up" and
// send the owner to /onboard. The tests in scripts/backendHealth.test.mjs pin
// these rules; components/shell/useBackendHealth.ts retries on them.

export interface BackendHealth {
  status: string;
  company_profile_loaded: boolean;
  company_name?: string | null;
}

/**
 * The health answer, or null when the response isn't one: not a 2xx, or a
 * body without a boolean `company_profile_loaded`.
 */
export function parseHealth(ok: boolean, body: unknown): BackendHealth | null {
  if (!ok || typeof body !== "object" || body === null) return null;
  const h = body as Record<string, unknown>;
  if (typeof h.company_profile_loaded !== "boolean") return null;
  return body as BackendHealth;
}

const FIRST_RETRY_MS = 1_000;
const LONGEST_RETRY_MS = 15_000;

/** How long to wait before asking again after `failures` misses in a row:
 * 1s, 2s, 4s, 8s, then every 15s. */
export function retryDelayMs(failures: number): number {
  const n = Math.max(1, Math.floor(failures));
  return Math.min(LONGEST_RETRY_MS, FIRST_RETRY_MS * 2 ** (n - 1));
}

/** Misses in a row before the page says it is reconnecting: one blip that
 * the first quick retry fixes shows nothing. */
export const RECONNECTING_AFTER = 2;

export const RECONNECTING_TEXT = "Reconnecting to your Executive…";
