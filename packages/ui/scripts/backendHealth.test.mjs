// A backend that can't be reached never reads as "not set up"
// (lib/backendHealth.ts).
import assert from "node:assert/strict";
import test from "node:test";

import { parseHealth, RECONNECTING_AFTER, retryDelayMs } from "../src/lib/backendHealth.ts";

test("a real answer is the answer, either way", () => {
  assert.deepEqual(parseHealth(true, { status: "ok", company_profile_loaded: true }), {
    status: "ok",
    company_profile_loaded: true,
  });
  assert.equal(parseHealth(true, { status: "ok", company_profile_loaded: false })?.company_profile_loaded, false);
});

test("an error status, an error body or no body says nothing about setup", () => {
  // The proxy's answer while the backend restarts: JSON, but not a health answer.
  assert.equal(parseHealth(false, { error: "backend unavailable" }), null);
  assert.equal(parseHealth(true, { error: "backend unavailable" }), null);
  // Even a health-shaped body on a failed status is not trusted.
  assert.equal(parseHealth(false, { status: "ok", company_profile_loaded: false }), null);
  // An HTML error page fails to parse and arrives as null.
  assert.equal(parseHealth(true, null), null);
  assert.equal(parseHealth(true, "Bad Gateway"), null);
  assert.equal(parseHealth(true, { company_profile_loaded: "false" }), null);
});

test("retries start quick and settle at every 15 seconds", () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6, 50].map(retryDelayMs), [1000, 2000, 4000, 8000, 15000, 15000, 15000]);
  assert.equal(retryDelayMs(0), 1000);
});

test("one blip shows nothing; a second miss says reconnecting", () => {
  assert.equal(RECONNECTING_AFTER, 2);
});
