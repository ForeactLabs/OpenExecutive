// Home's "I can also take on" list as the page reads it (lib/takeOnApi.ts).
import assert from "node:assert/strict";
import test from "node:test";
import { anyWorking, ownLine, parseOfferList, sourceLine, takeOnView } from "../src/lib/takeOnApi.ts";

test("offers are read with only what they should carry", () => {
  const list = parseOfferList({
    home_max: 3,
    offers: [
      {
        key: "made:abc", kind: "email", kind_label: "Email", title: "I can send Dana the proposal",
        source: "Your inbox", source_detail: "Dana, Nov 20",
        preview: [{ label: "To", value: "dana@client.example" }, { label: "", value: "x" }, "junk"],
        status: "card", decision_id: 42, done: [{ text: "Emailed Dana", link: "/sent/1" }, { link: "/x" }],
        changed: true, said: "cc Sam",
      },
      { key: "", title: "no key" },
      { key: "made:def", title: "I can book it", status: "launching" },
    ],
  });
  assert.equal(list.home_max, 3);
  assert.equal(list.making, false);
  assert.equal(parseOfferList({ offers: [], making: true }).making, true);
  assert.equal(list.offers.length, 2);
  const [a, b] = list.offers;
  assert.deepEqual(a.preview, [{ label: "To", value: "dana@client.example" }]);
  assert.deepEqual(a.done, [{ text: "Emailed Dana", link: "/sent/1" }]);
  assert.equal(a.status, "card");
  assert.equal(a.decision_id, 42);
  assert.equal(sourceLine(a), "Your inbox · Dana, Nov 20");
  assert.equal(b.status, "");
  assert.equal(anyWorking(list.offers), false);
  assert.equal(anyWorking([{ ...b, status: "working" }]), true);
});

test("an unreadable answer is an empty list with Home's three", () => {
  for (const raw of [null, undefined, "x", { offers: "no" }]) {
    assert.deepEqual(parseOfferList(raw), { offers: [], home_max: 3, making: false, on_its_own: [] });
  }
});

test("what it does on its own reads as one line", () => {
  const list = parseOfferList({ offers: [], on_its_own: ["drafting replies to your inbox", 3, "", "watching news and pages"] });
  assert.deepEqual(list.on_its_own, ["drafting replies to your inbox", "watching news and pages"]);
  assert.equal(ownLine(list.on_its_own), "drafting replies to your inbox, watching news and pages.");
  assert.equal(ownLine([]), "");
});

test("with nothing in it, Home leaves the section out and See all says so", () => {
  assert.equal(takeOnView(0, false), "hidden");
  assert.equal(takeOnView(0, true), "nothing");
  assert.equal(takeOnView(2, false), "list");
  assert.equal(takeOnView(1, true), "list");
});
