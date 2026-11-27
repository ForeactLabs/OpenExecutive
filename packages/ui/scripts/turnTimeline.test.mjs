import assert from "node:assert/strict";
import test from "node:test";
import {
  chipText,
  clock,
  duration,
  groupSteps,
  summaryLine,
  timelineFrom,
} from "../src/lib/turnTimeline.ts";

const done = {
  task: "",
  seconds: 52,
  done: true,
  steps: [
    { label: "Searched your email", kind: "look", at: 3, state: "done", detail: "4 emails" },
    { label: "Read a file", kind: "look", at: 9, state: "done" },
    { label: "Consulted specialists", kind: "work", at: 17, state: "done" },
    { label: "Send an email", kind: "do", at: 45, state: "waits", card_id: 7 },
  ],
  reads: ["4 emails", "1 file", "Your calendar"],
};

test("a saved timeline reads back, junk dropped", () => {
  const t = timelineFrom({ ...done, steps: [...done.steps, { label: "" }, 5, { label: "Odd", kind: "x", state: "y" }] });
  assert.equal(t.steps.length, 5);
  assert.equal(t.steps[4].kind, "look");
  assert.equal(t.steps[4].state, "done");
  assert.equal(t.steps[3].card_id, 7);
  assert.equal(timelineFrom(null), null);
  assert.equal(timelineFrom({ steps: [] }), null);
  assert.equal(timelineFrom("x"), null);
});

test("a finished turn has nothing still running", () => {
  const t = timelineFrom({ ...done, steps: [{ label: "A", kind: "look", at: 0, state: "current" }] });
  assert.equal(t.steps[0].state, "done");
});

test("the chip says the count and time, or what it is doing now", () => {
  assert.deepEqual(chipText(timelineFrom(done)), { now: null, count: "4 steps · 52s" });
  const live = timelineFrom({
    ...done,
    done: false,
    steps: [
      { label: "Read a file", kind: "look", at: 1, state: "done" },
      { label: "Running the numbers", kind: "work", at: 5, state: "current" },
    ],
  });
  assert.deepEqual(chipText(live, 19), { now: "Running the numbers", count: "1 step · 0:19" });
});

test("steps group by kind in time order", () => {
  const groups = groupSteps(timelineFrom(done));
  assert.deepEqual(
    groups.map((g) => [g.name, g.steps.length]),
    [["Looked at", 2], ["Worked out", 1], ["Did", 1]],
  );
});

test("the summary says what it read, worked out and what waits", () => {
  assert.equal(
    summaryLine(timelineFrom(done)),
    "Read 4 emails, 1 file and your calendar. Worked it through in 1 step. 1 step waits for your OK.",
  );
});

test("times read as people say them", () => {
  assert.equal(duration(52), "52s");
  assert.equal(duration(72), "1m 12s");
  assert.equal(duration(120), "2m");
  assert.equal(clock(19), "0:19");
  assert.equal(clock(75), "1:15");
});
