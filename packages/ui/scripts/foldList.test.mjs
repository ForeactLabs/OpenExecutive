// Long lists shown like Chats: what needs you first, then groups by day or person.
import assert from "node:assert/strict";
import { test } from "node:test";

import { dayOf, foldGroups, groupOpen, matches, shortWhen } from "../src/lib/foldList.ts";

const NOW = new Date(2026, 10, 28, 14, 30); // Sat Nov 28, 2:30 PM local
const at = (days, hour = 10) => new Date(2026, 10, 28 - days, hour).toISOString();
const e = (id, who, days, extra = {}) => ({ id, who, gist: `${id} gist`, at: at(days), tone: "plain", ...extra });

test("days fall by the viewer's calendar", () => {
  assert.equal(dayOf(at(0, 0), NOW), "today");
  assert.equal(dayOf(at(1, 23), NOW), "yesterday");
  assert.equal(dayOf(at(6), NOW), "week");
  assert.equal(dayOf(at(7), NOW), "week");
  assert.equal(dayOf(at(8), NOW), "earlier");
  assert.equal(dayOf("", NOW), "earlier");
  assert.equal(dayOf("not a date", NOW), "earlier");
});

test("what needs you comes out first; the rest group by day, newest first, empty days left out", () => {
  const items = [e("a", "Ann", 3), e("b", "Bo", 0), e("c", "Cy", 0, { needs: true }), e("d", "Di", 20), e("f", "Ann", 0)];
  const { needs, groups } = foldGroups(items, "day", NOW);
  assert.deepEqual(needs.map((i) => i.id), ["c"]);
  assert.deepEqual(groups.map((g) => g.label), ["Today", "Previous 7 days", "Earlier"]);
  assert.deepEqual(groups[0].items.map((i) => i.id).sort(), ["b", "f"]);
});

test("by person groups under the person, the one heard from last first, any case", () => {
  const items = [e("a", "Ann", 3), e("b", "Bo", 0), e("c", "ann", 1), e("d", "x", 0, { person: "Bo" })];
  const { groups } = foldGroups(items, "person", NOW);
  assert.deepEqual(groups.map((g) => [g.label, g.items.map((i) => i.id)]), [["Bo", ["b", "d"]], ["ann", ["c", "a"]]]);
});

test("search finds every word anywhere, in any case", () => {
  const item = e("a", "Curt Floener", 0, { gist: "Mom's birthday plans", tag: "Not sure it went", words: "4078644111" });
  assert.ok(matches(item, "curt birthday"));
  assert.ok(matches(item, "407864"));
  assert.ok(matches(item, "  "));
  assert.ok(!matches(item, "curt dinner"));
});

test("an item's time says the day only where its group doesn't", () => {
  const today = at(0, 9);
  assert.equal(shortWhen(today, { now: NOW }), shortWhen(today, { now: NOW, inDay: true }));
  assert.equal(shortWhen(at(1), { now: NOW }), "Yesterday");
  assert.notEqual(shortWhen(at(1), { now: NOW, inDay: true }), "Yesterday");
  assert.equal(shortWhen("", { now: NOW }), "");
});

test("a day the clocks change on is still one calendar day", () => {
  // US clocks go back on Nov 1 2026; 00:30 that night is still Yesterday on Nov 2.
  const now = new Date(2026, 10, 2, 8);
  assert.equal(dayOf(new Date(2026, 10, 1, 0, 30).toISOString(), now), "yesterday");
  assert.equal(dayOf(new Date(2026, 9, 26, 0, 30).toISOString(), now), "week");
  assert.equal(dayOf(new Date(2026, 9, 25, 23, 30).toISOString(), now), "earlier");
});

test("a group the person folds stays folded, even while searching", () => {
  const none = { folds: {}, searching: false, mode: "day" };
  assert.equal(groupOpen("day:today", 0, none), true);
  assert.equal(groupOpen("day:week", 1, none), false);
  assert.equal(groupOpen("person:ann", 0, { ...none, mode: "person" }), false);
  assert.equal(groupOpen("day:week", 1, { ...none, searching: true }), true);
  assert.equal(groupOpen("day:today", 0, { ...none, searching: true, folds: { "day:today": false } }), false);
});

test("a phone number matches however it is typed", () => {
  const item = e("a", "Dana", 0, { words: "(407) 864-4111" });
  for (const q of ["4078644111", "407-864-4111", "(407) 864", "864-4111"]) assert.equal(matches(item, q), true, q);
  assert.equal(matches(item, "555-0000"), false);
  // Digits from different parts don't run together.
  const texts = e("b", "Dana", 0, { words: "(407) 864-4111 see you at 5 · Order 123 and gate 456" });
  assert.equal(matches(texts, "1115"), false);
  assert.equal(matches(texts, "123456"), false);
  assert.equal(matches(texts, "864-4111"), true);
});
