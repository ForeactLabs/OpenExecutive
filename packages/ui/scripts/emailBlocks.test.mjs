import assert from "node:assert/strict";
import { test } from "node:test";

import { emailBlocks } from "../src/lib/emailBlocks.ts";
import { htmlLinks } from "../src/lib/htmlLinks.ts";

const EMAIL = [
  "Hi,",
  "",
  "| | ECA | CCG |",
  "|---|---|---:|",
  "| Price per unit | $46,850 | $42,031 |",
  "| Deposit | $30K |",
  "",
  "Thanks,",
].join("\n");

test("an email's pipe table reads as a table, the rest as written", () => {
  const blocks = emailBlocks(EMAIL);
  assert.deepEqual(blocks.map((b) => b.kind), ["text", "table", "text"]);
  assert.equal(blocks[0].text, "Hi,\n");
  assert.deepEqual(blocks[1].header, ["", "ECA", "CCG"]);
  assert.deepEqual(blocks[1].rows, [["Price per unit", "$46,850", "$42,031"], ["Deposit", "$30K", ""]]);
  assert.deepEqual(blocks[1].align, ["left", "left", "right"]);
  assert.equal(blocks[2].text, "\nThanks,");
});

test("a pipe without a separator row under it is just text", () => {
  assert.deepEqual(emailBlocks("a | b\nc | d"), [{ kind: "text", text: "a | b\nc | d" }]);
  assert.deepEqual(emailBlocks(""), [{ kind: "text", text: "" }]);
});

test("a reply's HTML link shows as a link, anything else as written", () => {
  assert.equal(
    htmlLinks('See <a href="https://mail.google.com/mail/u/?a=1#drafts?compose=1a" target="_blank">Open the draft</a>.'),
    "See [Open the draft](https://mail.google.com/mail/u/?a=1#drafts?compose=1a).",
  );
  assert.equal(htmlLinks('<a href="javascript:alert(1)">x</a>'), '<a href="javascript:alert(1)">x</a>');
  assert.equal(htmlLinks('<a href="https://x.example/a">[b]</a>'), "[b](https://x.example/a)");
  // Code stays exactly as written.
  const code = 'Use:\n```html\n<a href="https://x.example/c">Book</a>\n```\nor `<a href="https://x.example/c">Book</a>`';
  assert.equal(htmlLinks(code), code);
});

test("an attribute list after a link is dropped, other braces stay", () => {
  const url = "https://mail.google.com/mail/u/?authuser=a%40b.co#all/18f2";
  assert.equal(htmlLinks(`[Open the Nov 16 email](${url}){target="_blank"}`), `[Open the Nov 16 email](${url})`);
  assert.equal(htmlLinks(`[a](${url}){:target='_blank' rel=noopener}.`), `[a](${url}).`);
  assert.equal(htmlLinks("[w](https://x.example/a_(b)){.ext #x}"), "[w](https://x.example/a_(b))");
  assert.equal(htmlLinks('![c](https://x.example/c.png "C"){width=300}'), '![c](https://x.example/c.png "C")');
  // A link turned from HTML loses it too.
  assert.equal(htmlLinks('<a href="https://x.example/d">D</a>{target="_blank"}'), "[D](https://x.example/d)");
  // Braces that aren't an attribute list, or not right after a link, stay.
  assert.equal(htmlLinks("[a](https://x.example/) {target=\"_blank\"}"), "[a](https://x.example/) {target=\"_blank\"}");
  assert.equal(htmlLinks("[a](https://x.example/){see below}"), "[a](https://x.example/){see below}");
  assert.equal(htmlLinks("Use `[a](u){target=\"_blank\"}` in kramdown"), "Use `[a](u){target=\"_blank\"}` in kramdown");
});

test("a forwarded message reads as written, as the email sends it", () => {
  const fwd = "Here it is.\n\n---------- Forwarded message ---------\nFrom: A\n| a | b |\n|---|---|\n| 1 | 2 |";
  const blocks = emailBlocks(fwd);
  assert.ok(blocks.every((b) => b.kind === "text"));
  assert.equal(blocks.map((b) => b.text).join("\n"), fwd);
  const outlook = "| a | b |\n|---|---|\n| 1 | 2 |\n\nFrom: A\nSent: Monday\n| c | d |\n|---|---|";
  const [table, ...rest] = emailBlocks(outlook);
  assert.equal(table.kind, "table");
  assert.deepEqual(rest, [{ kind: "text", text: "\nFrom: A\nSent: Monday\n| c | d |\n|---|---|" }]);
});
