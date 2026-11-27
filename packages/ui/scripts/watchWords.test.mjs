import assert from "node:assert/strict";
import test from "node:test";
import { noiseWords, raiseTheBar, triggerInWords } from "../src/lib/watchWords.ts";

test("triggerInWords says when a watch tells you", () => {
  // Same lines as test_plain_lines in packages/core/tests/unit/test_watchlist_describe.py.
  assert.equal(triggerInWords("stock", {}), "It moves 5% or more in a day, up or down");
  assert.equal(triggerInWords("stock", { abs_change_pct_gte: 8 }), "It moves 8% or more in a day, up or down");
  assert.equal(
    triggerInWords("edgar", {}),
    "They file a 8-K (major news), 10-K (annual report) or 10-Q (quarterly report)",
  );
  assert.equal(
    triggerInWords("rss", { keywords: ["pricing", "launch"] }),
    "There's a new post that mentions “pricing” or “launch”",
  );
  assert.equal(triggerInWords("page_watch", null), "The page changes");
  assert.equal(triggerInWords("query", { keywords: ["layoffs"] }), "A new result turns up that mentions “layoffs”");
  assert.equal(triggerInWords("mystery", {}), "Something new turns up");
});

test("triggerInWords says when a watch only wants big changes", () => {
  assert.equal(
    triggerInWords("rss", { keywords: ["pricing"], only_big: true }),
    "There's a new post that mentions “pricing”, and only when it is a big change",
  );
  assert.equal(triggerInWords("page_watch", { only_big: true }), "The page changes, and only when it is a big change");
  // A stock's own threshold already says how big.
  assert.equal(triggerInWords("stock", { only_big: true }), "It moves 5% or more in a day, up or down");
});

test("raiseTheBar doubles a stock threshold", () => {
  const r = raiseTheBar({ signal_type: "stock", trigger_json: { abs_change_pct_gte: 5 } });
  assert.deepEqual(r?.patch, { trigger: { abs_change_pct_gte: 10 } });
  assert.equal(r?.label, "Only moves of 10%+");
  const empty = raiseTheBar({ signal_type: "stock", trigger_json: {} });
  assert.deepEqual(empty?.patch, { trigger: { abs_change_pct_gte: 10 } });
  const small = raiseTheBar({ signal_type: "stock", trigger_json: { abs_change_pct_gte: 0.4 } });
  assert.deepEqual(small?.patch, { trigger: { abs_change_pct_gte: 1 } });
  assert.equal(raiseTheBar({ signal_type: "stock", trigger_json: { abs_change_pct_gte: 100 } }), null);
});

test("raiseTheBar asks for big changes only otherwise, through the trigger", () => {
  const r = raiseTheBar({ signal_type: "rss", trigger_json: { keywords: ["x"] } });
  assert.deepEqual(r?.patch, { trigger: { keywords: ["x"], only_big: true } });
  assert.equal(r?.label, "Only big changes");
  assert.equal(r?.done, "You'll only hear about big changes from this.");
  assert.equal(raiseTheBar({ signal_type: "query", trigger_json: { only_big: true } }), null);
});

test("an email watch says who it is from and what its subject mentions, and has no bar to raise", () => {
  assert.equal(triggerInWords("mailbox", { from: ["pat@example.com"] }), "pat@example.com emails the Executive or you");
  assert.equal(
    triggerInWords("mailbox", { from: ["pat@example.com", "sam@example.com"], keywords: ["contract"] }),
    "pat@example.com or sam@example.com emails the Executive or you about “contract”",
  );
  assert.equal(
    triggerInWords("mailbox", { keywords: ["sign up"] }),
    "An email that mentions “sign up” reaches the Executive or you",
  );
  assert.equal(raiseTheBar({ signal_type: "mailbox", trigger_json: { keywords: ["sign up"] } }), null);
});

test("triggerInWords says when every change goes as a card", () => {
  assert.equal(triggerInWords("page_watch", { every_change: true }), "The page changes, every one as a card");
  // Only rss, page_watch and query place finds by closeness.
  assert.equal(triggerInWords("stock", { every_change: true }), "It moves 5% or more in a day, up or down");
});

test("noiseWords names dismissed and unopened cards", () => {
  assert.equal(noiseWords({ recent_alerts: 8, recent_dismissed: 6 }), "You dismissed 6 of the last 8 things this told you.");
  assert.equal(
    noiseWords({ recent_alerts: 8, recent_dismissed: 4, recent_expired_unread: 4 }),
    "You dismissed 4 and 4 went unopened of the last 8 things this told you.",
  );
  assert.equal(
    noiseWords({ recent_alerts: 8, recent_dismissed: 0, recent_expired_unread: 8 }),
    "8 went unopened of the last 8 things this told you.",
  );
});
