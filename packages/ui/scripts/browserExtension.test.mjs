// The Open Executive browser extension's shared rules (packages/extension/shared.js
// and site-rules.js):
// which fields it never types into, which pages it never touches, and which
// site counts as a connected Open Executive.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const rules = readFileSync(new URL("../../extension/site-rules.js", import.meta.url), "utf8");
const src = readFileSync(new URL("../../extension/shared.js", import.meta.url), "utf8");
const sandbox = { URL };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(rules, sandbox);
vm.runInContext(src, sandbox);
const OE = sandbox.OE;

test("never types into passwords, card numbers and codes", () => {
  assert.equal(OE.sensitiveField({ type: "password" }), true);
  assert.equal(OE.sensitiveField({ type: "text", autocomplete: "cc-number" }), true);
  assert.equal(OE.sensitiveField({ type: "tel", name: "cardNumber" }), true);
  assert.equal(OE.sensitiveField({ type: "text", placeholder: "CVV" }), true);
  assert.equal(OE.sensitiveField({ type: "text", autocomplete: "one-time-code" }), true);
  assert.equal(OE.sensitiveField({ type: "text", name: "company", placeholder: "Company name" }), false);
  assert.equal(OE.sensitiveField({ type: "text", name: "pwd" }), true);
  assert.equal(OE.sensitiveField({ type: "text", label: "API key" }), true);
  assert.equal(OE.sensitiveField({ type: "text", label: "IBAN" }), true);
  assert.equal(OE.sensitiveField({ type: "email", name: "email" }), false);
});

test("a sign-in code is secret, a zip or promo code is not", () => {
  assert.equal(OE.sensitiveField({ type: "text", name: "code" }), true);
  assert.equal(OE.sensitiveField({ type: "text", label: "Enter the code we sent you" }), true);
  assert.equal(OE.sensitiveField({ type: "text", name: "zip_code" }), false);
  assert.equal(OE.sensitiveField({ type: "text", label: "Promo code" }), false);
  assert.equal(OE.signInField({ type: "text", name: "code" }), true);
  assert.equal(OE.signInField({ type: "text", label: "Postal code" }), false);
});

test("payment frames and secret fields are blacked out in the screenshot", () => {
  assert.equal(OE.paymentFrame("https://js.stripe.com/v3/elements-inner-card.html", ""), true);
  assert.equal(OE.paymentFrame("https://www.youtube.com/embed/x", "Video"), false);
  assert.equal(OE.paymentFrame("", "Secure card payment input frame"), true);
  assert.deepEqual(JSON.parse(JSON.stringify(OE.scaledMasks([{ x: 10, y: 20, w: 100, h: 30 }, { x: "a" }], 0.5))), [
    { x: 5, y: 10, w: 51, h: 16 },
  ]);
  const bg = readFileSync(new URL("../../extension/background.js", import.meta.url), "utf8");
  const shot = bg.slice(bg.indexOf("async function screenshot"), bg.indexOf("// ---- Waiting on you"));
  // No masks from the page, no picture.
  assert.ok(shot.includes("!Array.isArray(snap.masks)"));
  assert.ok(shot.includes("ctx.fillRect"));
});

test("knows a sign-in form's fields", () => {
  assert.equal(OE.signInField({ type: "password" }), true);
  assert.equal(OE.signInField({ type: "text", autocomplete: "one-time-code" }), true);
  assert.equal(OE.signInField({ type: "email", autocomplete: "username" }), true);
  assert.equal(OE.signInField({ type: "email", name: "newsletter" }), false);
  assert.equal(OE.signInField({ type: "text", autocomplete: "cc-number" }), false);
});

test("a handed-over sign-in is done only once it has moved to a page with no sign-in form", () => {
  const start = "https://shop.example.com/login?next=/cart";
  assert.equal(OE.pastSignIn(start, "https://shop.example.com/cart", false), true);
  assert.equal(OE.pastSignIn(start, "https://shop.example.com/login?next=/cart#code", false), false);
  assert.equal(OE.pastSignIn(start, "https://shop.example.com/login/verify", true), false);
  assert.equal(OE.pastSignIn(start, "not a url", false), false);
});

test("the Waiting on you bar shows what it waits on as text, never markup", () => {
  const content = readFileSync(new URL("../../extension/content.js", import.meta.url), "utf8");
  const bar = content.slice(content.indexOf("function showHandBar"), content.indexOf("async function postHandover"));
  assert.ok(bar.includes("text.textContent = `Waiting on you: "));
  assert.ok(!bar.includes("innerHTML"));
});

test("knows email and chat sites", () => {
  assert.equal(OE.sendingSite("https://mail.google.com/mail/u/0/#inbox"), true);
  assert.equal(OE.sendingSite("https://app.slack.com/client/T1/C1"), true);
  assert.equal(OE.sendingSite("https://acme.slack.com/messages"), true);
  assert.equal(OE.sendingSite("https://outlook.office.com/mail/"), true);
  assert.equal(OE.sendingSite("https://www.linkedin.com/messaging/thread/1"), true);
  assert.equal(OE.sendingSite("https://www.linkedin.com/feed/"), true);
  assert.equal(OE.sendingSite("https://mail.google.com./"), true);
  assert.equal(OE.sendingSite("https://www.icloud.com/mail/"), true);
  assert.equal(OE.sendingSite("https://www.icloud.com/notes/"), false);
  assert.equal(OE.sendingSite("https://notslack.com/"), false);
  assert.equal(OE.sendingSite("https://www.salesforce.com/"), false);
});

test("never touches the browser's own pages or the extension stores", () => {
  assert.equal(OE.blockedUrl("chrome://settings"), true);
  assert.equal(OE.blockedUrl("file:///etc/passwd"), true);
  assert.equal(OE.blockedUrl("https://chromewebstore.google.com/detail/x"), true);
  assert.equal(OE.blockedUrl("https://chrome.google.com/webstore/x"), true);
  assert.equal(OE.blockedUrl("https://www.salesforce.com/"), false);
  assert.equal(OE.openableUrl("javascript:alert(1)"), false);
  assert.equal(OE.openableUrl("https://example.com/a"), true);
});

test("only a connected https site is an Open Executive", () => {
  const origins = ["https://oe.example.com"];
  assert.equal(OE.onConnected("https://oe.example.com/chat?x=1", origins), true);
  assert.equal(OE.onConnected("https://evil.example.com/", origins), false);
  assert.equal(OE.onConnected("http://oe.example.com/", origins), false);
  assert.equal(OE.onConnected("https://oe.example.com.evil.com/", origins), false);
});

test("a dropdown option is found by its text", () => {
  assert.equal(OE.pickOption(["Discovery", "Negotiation"], "negotiation"), 1);
  assert.equal(OE.pickOption(["Closed won", "Closed lost"], "lost"), 1);
  assert.equal(OE.pickOption(["A"], ""), -1);
});

test("the extension asks for nothing past what it uses", () => {
  const manifest = JSON.parse(readFileSync(new URL("../../extension/manifest.json", import.meta.url), "utf8"));
  assert.deepEqual([...manifest.permissions].sort(), ["alarms", "scripting", "storage", "tabGroups", "tabs"]);
  assert.equal(manifest.manifest_version, 3);
  assert.ok(!JSON.stringify(manifest).includes("debugger"));
});

test("only a plain path opens an Open Executive page", () => {
  assert.equal(OE.pagePath("/knowledge?tab=docs"), "/knowledge?tab=docs");
  assert.equal(OE.pagePath(" /today "), "/today");
  assert.equal(OE.pagePath("/apps"), "/apps");
  for (const walk of ["/x/../api/backend/x", "/./oauth", "/x/..", "/%2e%2e/api", "/x%2fapi", "/x%5capi"]) {
    assert.equal(OE.pagePath(walk), "", walk);
  }
  assert.equal(OE.pagePath("/knowledge?q=a/../b"), "/knowledge?q=a/../b");
  for (const bad of ["/api/account/export", "/oauth/authorize?x=1", "/MCP", "/.well-known/x", "//evil.com", "https://evil.com", "knowledge", "/a b", "/a\\b", "", null, "/" + "x".repeat(600)]) {
    assert.equal(OE.pagePath(bad), "", String(bad));
  }
});

test("clicks and types on email and chat sites only on an approved step", () => {
  const inbox = "https://mail.google.com/mail/u/0/#inbox";
  const waits = OE.sendingStop(inbox, {});
  assert.equal(waits.ok, false);
  assert.equal(waits.sending_site, true);
  assert.equal(waits.url, inbox);
  assert.equal(OE.sendingStop(inbox, { send_ok: "yes" }).sending_site, true);
  assert.equal(OE.sendingStop(inbox, { send_ok: true }), null);
  assert.equal(OE.sendingStop(inbox, { send_ok: true, at: inbox }), null);
  const moved = OE.sendingStop("https://mail.google.com/mail/u/0/#sent", { send_ok: true, at: inbox });
  assert.equal(moved.ok, false);
  assert.equal(moved.sending_site, undefined);
  assert.equal(OE.sendingStop("https://www.salesforce.com/", {}), null);
});

test("an Open Executive page opens in the tab they are working in, never the extension's own window", () => {
  const origin = "https://oe.example.com";
  const tabs = [
    { id: 1, windowId: 9, url: `${origin}/`, active: true, lastAccessed: 500 }, // the extension's own window
    { id: 2, windowId: 3, url: `${origin}/people`, active: false, lastAccessed: 300 },
    { id: 3, windowId: 3, url: "https://crm.example.com/", active: true, lastAccessed: 400 },
    { id: 4, windowId: 5, url: `${origin}/today`, active: true, lastAccessed: 100 },
    { id: 5, windowId: 3, url: "https://other.example.com/", active: false, lastAccessed: 900 },
  ];
  // Their window's front tab is another site: the Open Executive tab they used last.
  assert.equal(OE.workingTab(tabs, origin, { ownWindowId: 9, focusedWindowId: 3 }).id, 2);
  // Without lastAccessed (Chrome before 121): still one in the window they are in.
  const old = tabs.map((t) => ({ ...t, lastAccessed: undefined }));
  assert.equal(OE.workingTab([old[3], ...old], origin, { ownWindowId: 9, focusedWindowId: 3 }).id, 2);
  // In front of them in the window they are in.
  assert.equal(OE.workingTab(tabs, origin, { ownWindowId: 9, focusedWindowId: 5 }).id, 4);
  // Only the extension's own window has one: none of theirs.
  assert.equal(OE.workingTab([tabs[0]], origin, { ownWindowId: 9, focusedWindowId: 3 }), null);
  // Another company's site is never theirs here.
  assert.equal(OE.workingTab(tabs, "https://acme.example.com", { ownWindowId: 9, focusedWindowId: 3 }), null);
});

// ---- The window beside the chat (background.js, with a stand-in `chrome`) ----

function fakeBrowser() {
  const store = {};
  const local = {};
  const wins = new Map([[1, { id: 1, state: "normal", left: 0, top: 0, width: 1000, height: 800, focused: true, tabs: [{ id: 11, active: true, windowId: 1 }] }]]);
  const removed = [];
  let next = 2;
  const tick = () => new Promise((ok) => setTimeout(ok, 1));
  const any = new Proxy(() => {}, {
    get: (_t, key) => (key === "then" ? undefined : key === "addListener" ? () => {} : any),
    apply: () => Promise.resolve(undefined),
  });
  const chrome = {
    storage: {
      session: {
        get: async (keys) => Object.fromEntries((Array.isArray(keys) ? keys : [keys]).map((k) => [k, store[k]])),
        set: async (vals) => void Object.assign(store, vals),
      },
      local: {
        get: async (keys) => Object.fromEntries((Array.isArray(keys) ? keys : [keys]).map((k) => [k, local[k]])),
        set: async (vals) => void Object.assign(local, vals),
      },
      onChanged: any,
    },
    windows: {
      get: async (id) => {
        await tick();
        if (!wins.has(id)) throw new Error("No window");
        return { ...wins.get(id) };
      },
      getLastFocused: async () => ({ ...wins.get(1) }),
      getAll: async () => [...wins.values()].map((w) => ({ ...w })),
      create: async (opts) => {
        await tick();
        const id = next++;
        const win = { id, state: "normal", left: opts.left, top: opts.top, width: opts.width, height: opts.height, focused: false, tabs: [{ id: id * 10, active: true, windowId: id }] };
        wins.set(id, win);
        return { ...win };
      },
      update: async (id, props) => {
        await tick();
        Object.assign(wins.get(id), props);
      },
      onRemoved: { addListener: (fn) => removed.push(fn) },
    },
    tabs: {
      query: async ({ windowId }) => (wins.get(windowId)?.tabs || []).map((t) => ({ ...t })),
      onRemoved: any,
      onUpdated: any,
    },
    alarms: any,
    runtime: any,
  };
  const sandbox = { URL, chrome, setTimeout, clearTimeout, console, importScripts: () => {
    vm.runInContext(rules, sandbox);
    vm.runInContext(src, sandbox);
  } };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(new URL("../../extension/background.js", import.meta.url), "utf8"), sandbox);
  const close = async (id) => {
    wins.delete(id);
    for (const fn of removed) fn(id);
    for (let i = 0; i < 20; i++) await tick();
  };
  return { bg: sandbox, wins, store, local, close };
}

test("texts: one site on and another off keeps Texts on, and a disconnected site's answer is dropped", async () => {
  const { bg, local } = fakeBrowser();
  const both = ["https://a.example.com", "https://b.example.com"];
  await bg.setTextsOn("https://a.example.com", true, both);
  await bg.setTextsOn("https://b.example.com", false, both);
  await bg.setTextsOn("https://a.example.com", true, both);
  await bg.setTextsOn("https://b.example.com", false, both);
  assert.equal(local.textsOn, true);
  // Only b is connected now: a's old "on" goes, and so does Texts.
  await bg.setTextsOn("https://b.example.com", false, ["https://b.example.com"]);
  assert.equal(local.textsOn, false);
  assert.deepEqual({ ...local.textsOnBy }, { "https://b.example.com": false });
});

test("two pages asked for at once open one window beside the chat, and closing it puts theirs back", async () => {
  const { bg, wins, store, close } = fakeBrowser();
  const open = () => bg.oneAtATime(async () => (await bg.sideTab()) || (await bg.makeSideWindow("https://example.com/")));
  await Promise.all([open(), open()]);
  assert.equal(wins.size, 2);
  assert.equal(wins.get(1).width, 500);
  const side = store.sideWindowId;
  assert.equal(wins.get(side).left, 500);
  assert.equal(wins.get(side).width, 500);
  assert.equal(store.sideRestore.width, 1000);
  await close(side);
  assert.equal(wins.get(1).width, 1000);
  assert.equal(store.sideWindowId, null);
});

test("a side window closed unheard still puts their window back", async () => {
  const { bg, wins, store } = fakeBrowser();
  await bg.oneAtATime(() => bg.makeSideWindow("https://example.com/"));
  wins.delete(store.sideWindowId);
  assert.equal(await bg.sideWindowId(), null);
  assert.equal(wins.get(1).width, 1000);
});

test("the site lists come from site-rules.js, so an operator can change them", () => {
  const box = { URL };
  box.globalThis = box;
  vm.createContext(box);
  vm.runInContext('globalThis.OE_SITE_RULES = { sendingHosts: ["Chat.Example.com"], sendingPaths: [["example.org", "/Inbox"]] };', box);
  vm.runInContext(src, box);
  assert.equal(box.OE.sendingSite("https://chat.example.com/room"), true);
  assert.equal(box.OE.sendingSite("https://example.org/inbox/1"), true);
  assert.equal(box.OE.sendingSite("https://mail.google.com/"), false);
  assert.equal(box.OE.paymentFrame("https://js.stripe.com/frame", ""), false);
  assert.equal(OE.paymentFrame("https://js.stripe.com/frame", ""), true);
});

test("every script list loads site-rules.js before shared.js", () => {
  const read = (f) => readFileSync(new URL(`../../extension/${f}`, import.meta.url), "utf8");
  const manifest = JSON.parse(read("manifest.json"));
  assert.deepEqual(manifest.content_scripts[0].js, ["site-rules.js", "shared.js", "content.js"]);
  assert.match(read("background.js"), /importScripts\("site-rules\.js", "shared\.js"\)/);
  for (const f of ["background.js", "popup.js"]) {
    assert.doesNotMatch(read(f), /files: \["shared\.js"/, f);
  }
  const html = read("popup.html");
  assert.ok(html.indexOf('src="site-rules.js"') < html.indexOf('src="shared.js"'));
});

test("the extension and the site agree on the steps' version", () => {
  const py = readFileSync(new URL("../../core/openexecutive/api/routes/browser.py", import.meta.url), "utf8");
  assert.equal(Number(/^PROTOCOL = (\d+)$/m.exec(py)?.[1]), OE.PROTOCOL);
});

test("the build stamps your name and version and ships every file", async () => {
  const { options, stamp, build } = await import("../../extension/build.mjs");
  const { mkdtempSync, readdirSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  assert.throws(() => options(["--version", "v1"], {}), /--version/);
  assert.throws(() => options(["--nope"], {}), /Unknown option/);
  assert.deepEqual(options(["--zip"], { EXTENSION_NAME: "Acme" }).name, "Acme");
  const m = stamp({ name: "Open Executive", version: "0.1", action: { default_popup: "popup.html" } }, { name: "Acme Executive", version: "2.0.1" });
  assert.equal(m.name, "Acme Executive");
  assert.equal(m.version, "2.0.1");
  assert.equal(m.action.default_title, "Acme Executive");
  assert.equal(m.action.default_popup, "popup.html");
  const out = join(mkdtempSync(join(tmpdir(), "oe-ext-")), "dist");
  const done = build({ ...options(["--out", out, "--zip"], {}), version: "9.9.9" });
  const shipped = readdirSync(out).sort();
  const source = readdirSync(new URL("../../extension/", import.meta.url))
    .filter((f) => !["build.mjs", ".gitignore", "README.md"].includes(f))
    .sort();
  assert.deepEqual(shipped, source);
  assert.equal(JSON.parse(readFileSync(join(out, "manifest.json"), "utf8")).version, "9.9.9");
  assert.equal(readFileSync(done.zip).readUInt32LE(0), 0x04034b50);
});

test("every step answers in time, even one stuck in Chrome, so the tab goes on asking", async () => {
  const bg = readFileSync(new URL("../../extension/background.js", import.meta.url), "utf8");
  const ms = Number(/const STEP_MS = (\d+);/.exec(bg)[1]);
  // Below the server's wait for a step (browser_relay.STEP_TIMEOUT_S, 45 s).
  assert.ok(ms > 0 && ms < 45000);
  assert.ok(bg.includes("await withinStepTime((late) => run(msg.step || {}, sender.tab, late))"));
  assert.ok(bg.includes("if (late.on) return"));
  const fn = bg.slice(bg.indexOf("function withinStepTime"), bg.indexOf("chrome.runtime.onMessage.addListener"));
  const box = { setTimeout, clearTimeout, Promise };
  vm.runInNewContext(`${fn.replace(/STEP_MS/g, "20")}; globalThis.withinStepTime = withinStepTime;`, Object.assign(box, { globalThis: box }));
  let seen;
  const stuck = await box.withinStepTime((late) => {
    seen = late;
    return new Promise(() => undefined);
  });
  assert.equal(stuck.ok, false);
  assert.match(stuck.error, /didn't finish/);
  // Told it is late, so it acts on nothing after answering.
  assert.equal(seen.on, true);
  assert.deepEqual(await box.withinStepTime(() => Promise.resolve({ ok: true })), { ok: true });
  // A page Chrome isn't drawing still answers the screenshot's snap.
  const content = readFileSync(new URL("../../extension/content.js", import.meta.url), "utf8");
  const snap = content.slice(content.indexOf('if (msg.type === "snap")'), content.indexOf('if (msg.type === "view")'));
  assert.ok(snap.includes("requestAnimationFrame(() => setTimeout(answer, 30))"));
  assert.ok(snap.includes("setTimeout(answer, 300)"));
});

// ---- Texts: the script on Google Messages (packages/extension/texts.js) ----

function textsHelpers() {
  const box = {};
  box.globalThis = box;
  box.window = box;
  box.top = box;
  vm.createContext(box);
  vm.runInContext(readFileSync(new URL("../../extension/texts.js", import.meta.url), "utf8"), box);
  return box.OE_TEXTS;
}

test("texts: the first look only counts unread texts from others, later looks count what changed", () => {
  const T = textsHelpers();
  const rows = [
    { key: "1", title: "Sam Lee", snippet: "Running late", unread: true },
    { key: "2", title: "+1 (555) 010-2000", snippet: "Old news", unread: false },
    { key: "3", title: "Ana", snippet: "You: see you there", unread: true },
  ];
  const first = T.changes(rows, null, 0);
  assert.deepEqual([...first.items.map((i) => i.conversation)], ["1"]);
  assert.equal(first.items[0].sender, "Sam Lee");
  assert.equal(first.items[0].outgoing, false);
  // Nothing changed: nothing new.
  assert.equal(T.changes(rows, first.seen, 1).items.length, 0);
  // A new text in a read conversation, and their own reply, both count once.
  const later = T.changes(
    [
      { key: "2", title: "+1 (555) 010-2000", snippet: "Are you in?", unread: false },
      { key: "3", title: "Ana", snippet: "You: on my way", unread: false },
    ],
    first.seen,
    2,
  );
  const [fromNumber, mine] = later.items;
  assert.equal(fromNumber.number, "+1 (555) 010-2000");
  assert.equal(fromNumber.text, "Are you in?");
  // Only a hint: the words stay as shown, and who it is with stays named.
  assert.equal(mine.outgoing, true);
  assert.equal(mine.text, "You: on my way");
  assert.equal(mine.sender, "Ana");
});

test("texts: a group's sender is left blank, and the new-conversation link is not a conversation", () => {
  const T = textsHelpers();
  const out = T.changes([{ key: "g", title: "Sam, Ana", snippet: "Pizza?", unread: true, group: true }, { key: "s", title: "Smith, John", snippet: "Hi", unread: true }, { key: "new", title: "x", snippet: "y", unread: true }], null, 0);
  assert.equal(out.items.length, 2);
  assert.equal(out.items[0].is_group, true);
  assert.equal(out.items[0].sender, "");
  // A comma in a name is still one person.
  assert.equal(out.items[1].is_group, false);
  assert.equal(out.items[1].sender, "Smith, John");
  assert.equal(T.keyOf("https://messages.google.com/web/conversations/new"), "");
  assert.equal(T.keyOf("https://messages.google.com/web/conversations/abc%3D1?x=1"), "abc=1");
});

test("texts: the script runs on Google Messages only and ships in the build", () => {
  const manifest = JSON.parse(readFileSync(new URL("../../extension/manifest.json", import.meta.url), "utf8"));
  const entry = manifest.content_scripts.find((c) => c.js.includes("texts.js"));
  assert.deepEqual(entry.matches, ["https://messages.google.com/*"]);
  assert.ok(manifest.description.length <= 132);
  const build = readFileSync(new URL("../../extension/build.mjs", import.meta.url), "utf8");
  assert.match(build, /"texts\.js"/);
  // The background takes texts only from Google Messages, and a send only from a connected Open Executive tab.
  const bg = readFileSync(new URL("../../extension/background.js", import.meta.url), "utf8");
  const seen = bg.slice(bg.indexOf('msg.type === "texts_seen"'));
  assert.ok(seen.slice(0, 400).includes("onMessages"));
});

test("texts: one send at a time, and only into the conversation it is for", () => {
  const bg = readFileSync(new URL("../../extension/background.js", import.meta.url), "utf8");
  assert.match(bg, /textQueue = run\.catch/);
  const texts = readFileSync(new URL("../../extension/texts.js", import.meta.url), "utf8");
  assert.ok(texts.includes("T.keyOf(location.href) !== String(msg.conversation)"));
});

test("texts: the same words again later are a new text", () => {
  const T = textsHelpers();
  const rows = (snippet) => [{ key: "1", title: "Sam Lee", snippet, unread: true }];
  const a = T.changes(rows("ok"), null, 1000);
  const b = T.changes(rows("You: sure"), a.seen, 2000);
  const c = T.changes(rows("ok"), b.seen, 3000);
  assert.equal(c.items.length, 1);
  assert.notEqual(c.items[0].ref, a.items[0].ref);
});

test("texts: off forgets what was seen, so on again only counts unread texts", () => {
  const texts = readFileSync(new URL("../../extension/texts.js", import.meta.url), "utf8");
  assert.ok(texts.includes("await chrome.storage.local.remove(SEEN_KEY);"));
  const bg = readFileSync(new URL("../../extension/background.js", import.meta.url), "utf8");
  assert.ok(bg.includes('await chrome.storage.local.remove("textsSeen");'));
});

test("texts: once Send is pressed a missing answer is unsure, and a new number picks only its Send-to choice", () => {
  const texts = readFileSync(new URL("../../extension/texts.js", import.meta.url), "utf8");
  assert.match(texts, /if \(!went\) return \{ ok: false, unsure: true/);
  assert.ok(!texts.includes("mw-contact-row button"));
  const content = readFileSync(new URL("../../extension/content.js", import.meta.url), "utf8");
  assert.ok(content.includes('ok ? "sent" : unsure ? "unsure" : "failed"'));
});

test("texts: new texts count as seen only once the site took them", () => {
  const texts = readFileSync(new URL("../../extension/texts.js", import.meta.url), "utf8");
  const report = texts.slice(texts.indexOf("async function report"), texts.indexOf("let timer = null"));
  const send = report.indexOf('type: "texts_seen"');
  const save = report.lastIndexOf("chrome.storage.local.set({ [SEEN_KEY]: seen })");
  assert.ok(send > 0 && save > send);
  assert.ok(report.includes("(answer && answer.ok === true) || !items.length"));
  assert.ok(!report.includes("[SEEN_KEY]: out.seen"));
});

test("texts: a send never takes the person's own tab from under a half-typed text", () => {
  const bg = readFileSync(new URL("../../extension/background.js", import.meta.url), "utf8");
  const one = bg.slice(bg.indexOf("async function sendOneText"), bg.indexOf("function waitLoaded"));
  assert.ok(one.indexOf('{ type: "texts_busy" }') > 0 && one.indexOf('{ type: "texts_busy" }') < one.indexOf("chrome.tabs.update(tab.id, { url })"));
  const texts = readFileSync(new URL("../../extension/texts.js", import.meta.url), "utf8");
  assert.ok(texts.includes('msg.type === "texts_busy"'));
});

test("texts: the conversation is checked again right before Send", () => {
  const texts = readFileSync(new URL("../../extension/texts.js", import.meta.url), "utf8");
  const body = texts.slice(texts.indexOf("async function send(msg)"), texts.indexOf("chrome.runtime.onMessage.addListener((msg, sender, reply)"));
  const check = body.indexOf("if (!key || T.keyOf(location.href) !== key || !box.isConnected)");
  assert.ok(check > body.indexOf("setValue(box, text)") && check < body.indexOf("button.click()"));
});

test("texts: a Google Messages page that never answered fails the send, never unsure", () => {
  const bg = readFileSync(new URL("../../extension/background.js", import.meta.url), "utf8");
  assert.ok(bg.includes('if (!ready) return { ok: false, error: "Google Messages didn\'t load. Nothing went." };'));
});

test("texts: a new number is sent to only through its own Send-to choice, never Enter", () => {
  const texts = readFileSync(new URL("../../extension/texts.js", import.meta.url), "utf8");
  const start = texts.slice(texts.indexOf("async function startConversation"), texts.indexOf("async function send("));
  assert.ok(!start.includes("pressEnter"));
  assert.ok(start.includes("T.sameNumber(textOf(el), number)"));
  assert.ok(!start.includes("mw-contact-selector-button"));
});

test("texts: the Send-to choice must show the same number, not one containing it", () => {
  const T = textsHelpers();
  assert.equal(T.sameNumber("Send to +1 (555) 010-0123", "5550100123"), true);
  assert.equal(T.sameNumber("Send to 0555 010 0123", "+1 555 010 0123"), true);
  assert.equal(T.sameNumber("Send to +1 555 010 01234", "555 010 0123"), false);
  assert.equal(T.sameNumber("Send to 12", "12"), false);
  assert.equal(T.sameNumber("", ""), false);
});
