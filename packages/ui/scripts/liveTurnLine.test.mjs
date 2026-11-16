// The running turn's one live line (lib/turnSteps.ts liveLineKind). Run by `npm test`.
import assert from "node:assert/strict";
import test from "node:test";

import { liveLineKind } from "../src/lib/turnSteps.ts";

const base = { isLoading: true, steps: [], hasProgress: false, inCommittee: false, statusShown: true };

test("a long turn shows its steps line only, never the plain status too", () => {
  assert.equal(liveLineKind({ ...base, steps: ["Opening the page…", "Looking at the page…"] }), "steps");
  assert.equal(liveLineKind({ ...base, hasProgress: true }), "steps");
});

test("a short turn shows the plain status when it has something to say", () => {
  assert.equal(liveLineKind({ ...base, steps: ["Looking at the page…"] }), "status");
  assert.equal(liveLineKind({ ...base, statusShown: false }), null);
  assert.equal(liveLineKind({ ...base, steps: ["a", "b"], inCommittee: true }), "status");
  assert.equal(liveLineKind({ ...base, isLoading: false, steps: ["a", "b"], statusShown: false }), null);
});
