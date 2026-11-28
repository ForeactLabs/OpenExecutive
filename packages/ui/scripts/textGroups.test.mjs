// The Texts page's sections: Needs a look, Texted for you, Just read (folded).
import assert from "node:assert/strict";
import { test } from "node:test";

import { groupOf, textGroups } from "../src/lib/textGroups.ts";

const t = (id, status) => ({ id, who: id, number: "", is_group: false, at: "", note: "", status, texts: [] });

test("each status lands in its section", () => {
  assert.equal(groupOf("failed"), "look");
  assert.equal(groupOf("unsure"), "look");
  assert.equal(groupOf("sent"), "texted");
  assert.equal(groupOf("sending"), "texted");
  assert.equal(groupOf("read"), "read");
});

test("sections come in order, keep the list's order, and empty ones are left out", () => {
  const groups = textGroups([t("ups", "read"), t("dana", "unsure"), t("priya", "sent"), t("dr", "read"), t("sam", "sending")]);
  assert.deepEqual(groups.map((g) => g.key), ["look", "texted", "read"]);
  assert.deepEqual(groups.map((g) => g.threads.map((x) => x.id)), [["dana"], ["priya", "sam"], ["ups", "dr"]]);
  assert.deepEqual(textGroups([t("a", "read")]).map((g) => g.key), ["read"]);
  assert.deepEqual(textGroups([]), []);
});

test("just read starts folded; what needs a look all shows", () => {
  const [look, texted, read] = textGroups([t("a", "failed"), t("b", "sent"), t("c", "read")]);
  assert.equal(read.shownAtFirst, 0);
  assert.equal(look.shownAtFirst, Infinity);
  assert.ok(texted.shownAtFirst > 0);
});
