// A held step's card says it's working once a button is pressed, instead of
// only greying out (a text can take up to half a minute to go), then says
// it's done before the card goes.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the pressed option shows it's working, then done", () => {
  const src = read("../src/components/briefing/StepTalk.tsx");
  assert.match(src, /running === o\.label \?/);
  assert.match(src, /"Working on it…"/);
  assert.match(src, /done \? "Done"/);
  assert.match(src, /aria-busy=\{running === o\.label\}/);
  // The card goes only after "Done" has shown.
  assert.match(src, /setTimeout\(onResolved, DONE_SHOWN_MS\)/);
  // Skip says so too, and goes at once.
  assert.match(src, /"Skipping…"/);
});

test("the edit forms' go buttons say they're working", () => {
  assert.match(read("../src/components/briefing/LeadTrainingCard.tsx"), /busy \? "Working on it…" : "Do it"/);
  assert.match(read("../src/components/HeldFieldsEdit.tsx"), /busy \? "Working on it…" : "Approve"/);
});
