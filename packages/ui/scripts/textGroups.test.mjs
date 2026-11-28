// Which texts need a look on the Texts page (they stay open at the top).
import assert from "node:assert/strict";
import { test } from "node:test";

import { groupOf } from "../src/lib/textGroups.ts";

test("each status lands in its group", () => {
  assert.equal(groupOf("failed"), "look");
  assert.equal(groupOf("unsure"), "look");
  assert.equal(groupOf("sent"), "texted");
  assert.equal(groupOf("sending"), "texted");
  assert.equal(groupOf("read"), "read");
});
