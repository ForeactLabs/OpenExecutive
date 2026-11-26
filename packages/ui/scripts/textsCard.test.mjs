// Delegate → As you → Texts (components/delegate/TextsCard.tsx): how it
// keeps up while the person pairs Google Messages in another tab.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const card = readFileSync(new URL("../src/components/delegate/TextsCard.tsx", import.meta.url), "utf8");

test("texts card: looks more often only while waiting on the phone, with the extension on", () => {
  assert.ok(card.includes("waiting.current = Boolean(state?.enabled && !state.paired && browser?.connected);"));
  assert.ok(card.includes("waiting.current ? PAIRING_RECHECK_MS : RECHECK_MS"));
  assert.ok(card.includes("{state.enabled && extension && ("));
});

test("texts card: an older answer never lands after a newer one", () => {
  assert.ok(card.includes("const look = ++asked;"));
  assert.ok(card.includes("if (look < textsSeen) return;"));
  assert.ok(card.includes("if (look < browserSeen) return;"));
});

test("texts card: a hidden tab waits, and coming back looks at once", () => {
  assert.ok(card.includes('if (document.visibilityState === "visible") load();'));
  assert.ok(card.includes('document.addEventListener("visibilitychange", back);'));
  assert.ok(card.includes('document.removeEventListener("visibilitychange", back);'));
});

test("texts card: sits right under How far it goes as you", () => {
  const acts = readFileSync(new URL("../src/components/settings/ActAsMeCard.tsx", import.meta.url), "utf8");
  const page = readFileSync(new URL("../src/app/delegate/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes("texts={<TextsCard />}"));
  const grid = acts.slice(acts.indexOf("<ReachCard"));
  assert.ok(grid.indexOf("{texts}") > 0 && grid.indexOf("{texts}") < grid.indexOf("<CalendarSection"));
});
