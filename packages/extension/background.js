// Open Executive browser extension: the background.
//
// Takes a step from a connected Open Executive tab's relay (content.js),
// does it in the tab the person is looking at (or opens and switches tabs
// itself), and answers with what happened. A look also sends a screenshot
// of what is on screen, and a click can land on a point of it. On an Open Executive tab it only
// looks, scrolls, points things out and opens its pages (the person presses
// its buttons); never acts on the browser's own pages or the extension
// stores, and holds every step for a while after the person presses Stop.

importScripts("site-rules.js", "shared.js");
const OE = globalThis.OE;

let stoppedUntil = 0;
// Stop survives the background going to sleep.
const restored = chrome.storage.session
  .get("stoppedUntil")
  .then(({ stoppedUntil: saved }) => {
    if (typeof saved === "number") stoppedUntil = Math.max(stoppedUntil, saved);
  })
  .catch(() => undefined);

async function connectedOrigins() {
  const { origins } = await chrome.storage.local.get("origins");
  return Array.isArray(origins) ? origins : [];
}

// The tab the person is looking at: the active tab of the browser window
// they used last (a popup window is not a browser window). A saved
// workflow's step (`window: "own"`) works in the extension's own window
// instead, so it never takes over their screen.
async function targetTab(own = false, side = false) {
  if (own) return await ownTab();
  if (side) {
    const tab = await sideTab();
    if (tab) return tab;
  }
  const win = await personWindow();
  return win?.tabs?.find((t) => t.active) || null;
}

// The browser window the person is working in: the one they used last, but
// never the extension's own (kept minimized for workflows).
async function personWindow() {
  const ownId = await ownWindowId();
  const sideId = await sideWindowId();
  const last = await chrome.windows.getLastFocused({ populate: true, windowTypes: ["normal"] }).catch(() => null);
  if (last && last.id !== ownId && last.id !== sideId) return last;
  const all = await chrome.windows.getAll({ populate: true, windowTypes: ["normal"] });
  const theirs = all.filter((w) => w.id !== ownId && w.id !== sideId);
  return theirs.find((w) => w.focused) || theirs.find((w) => w.state !== "minimized") || theirs[0] || null;
}

// A page opened in a new window, only when the person asked for one.
async function newWindowTab(url) {
  const win = await chrome.windows.create({ url, focused: true, type: "normal" });
  return win.tabs[0].id;
}

// ---- Its own window ----
//
// Workflow steps run in one "Open Executive" window the extension keeps,
// minimized, beside the person's own. While Chrome is open it also keeps an
// Open Executive tab pinned there (unless they turned that off or closed the
// window), so a workflow can reach the browser without them opening one.
// Google Messages, kept for texts, sits there in the "Open Executive" group
// and, like the pinned tab, is never a page a step works on.

async function ownWindowId() {
  const { ownWindowId: id } = await chrome.storage.session.get("ownWindowId");
  if (typeof id !== "number") return null;
  const win = await chrome.windows.get(id).catch(() => null);
  return win ? id : null;
}

async function makeOwnWindow(url) {
  const win = await chrome.windows.create({ url, focused: false, state: "minimized", type: "normal" });
  await chrome.storage.session.set({ ownWindowId: win.id, ownClosed: false });
  return win;
}

// The page a workflow step works on: the active tab of its own window, or
// its last one, never the Open Executive tab kept there.
async function ownTab() {
  const id = await ownWindowId();
  if (id === null) return null;
  const origins = await connectedOrigins();
  // Google Messages (kept there for texts, in the group) is never a page to work on.
  const work = (await chrome.tabs.query({ windowId: id })).filter((t) => !t.pinned && !onMessages(t.url) && !OE.onConnected(t.url, origins));
  return work.find((t) => t.active) || work[work.length - 1] || null;
}

// ---- Its window beside the chat ----
//
// A step from a chat (`window: "side"`) works in a window beside the
// person's, so the chat stays in view while it works: the first page it
// opens makes their window the left half of where it was and opens on the
// right half, in the Open Executive group. Later pages open there too. When
// they close it, their window goes back to how it was.

async function sideWindowId() {
  const { sideWindowId: id, sideRestore: was } = await chrome.storage.session.get(["sideWindowId", "sideRestore"]);
  if (typeof id !== "number") return null;
  const win = await chrome.windows.get(id).catch(() => null);
  if (win) return id;
  // Closed without our hearing of it: put their window back now, so the
  // next side window never takes the half size as theirs.
  await chrome.storage.session.set({ sideWindowId: null, sideRestore: null });
  await restoreTheirs(was);
  return null;
}

async function sideTab() {
  const id = await sideWindowId();
  if (id === null) return null;
  const tabs = await chrome.tabs.query({ windowId: id });
  return tabs.find((t) => t.active) || null;
}

// Two pages asked for at once must not both halve their window: opening a
// page there goes one at a time.
let sideQueue = Promise.resolve();
function oneAtATime(fn) {
  const run = sideQueue.then(fn, fn);
  sideQueue = run.catch(() => undefined);
  return run;
}

async function makeSideWindow(url) {
  const theirs = await personWindow();
  if (!theirs) {
    const win = await chrome.windows.create({ url, focused: true, type: "normal" });
    await chrome.storage.session.set({ sideWindowId: win.id, sideRestore: null });
    return win;
  }
  const was = {
    id: theirs.id,
    state: theirs.state,
    left: theirs.left,
    top: theirs.top,
    width: theirs.width,
    height: theirs.height,
  };
  if (theirs.state === "fullscreen" || theirs.state === "maximized" || theirs.state === "minimized") {
    await chrome.windows.update(theirs.id, { state: "normal" });
    // Leaving full screen animates on a Mac; sizing it before then is lost.
    if (theirs.state === "fullscreen") await new Promise((ok) => setTimeout(ok, 900));
  }
  // A minimized window's bounds are stale: size from where it came back to.
  const at = theirs.state === "minimized" ? (await chrome.windows.get(theirs.id).catch(() => null)) || was : was;
  const half = Math.floor(at.width / 2);
  await chrome.windows.update(theirs.id, { left: at.left, top: at.top, width: half, height: at.height });
  let win;
  try {
    win = await chrome.windows.create({
      url,
      type: "normal",
      focused: false,
      left: at.left + half,
      top: at.top,
      width: at.width - half,
      height: at.height,
    });
  } catch (err) {
    // No window beside theirs: put theirs back as it was.
    await restoreTheirs(was);
    throw err;
  }
  await chrome.storage.session.set({ sideWindowId: win.id, sideRestore: was });
  // Typing stays in their chat.
  await chrome.windows.update(theirs.id, { focused: true }).catch(() => undefined);
  return win;
}

chrome.windows.onRemoved.addListener((id) => {
  void chrome.storage.session.get(["sideWindowId", "sideRestore"]).then(async ({ sideWindowId: side, sideRestore: was }) => {
    if (side !== id) return;
    await chrome.storage.session.set({ sideWindowId: null, sideRestore: null });
    await restoreTheirs(was);
  });
});

async function restoreTheirs(was) {
  if (!was || typeof was.id !== "number") return;
  const theirs = await chrome.windows.get(was.id).catch(() => null);
  if (!theirs) return;
  await chrome.windows
    .update(was.id, { left: was.left, top: was.top, width: was.width, height: was.height })
    .catch(() => undefined);
  if (was.state === "maximized" || was.state === "fullscreen") {
    await chrome.windows.update(was.id, { state: was.state }).catch(() => undefined);
  }
}

async function keepConnected() {
  const { stayConnected } = await chrome.storage.local.get("stayConnected");
  if (stayConnected === false) return;
  const { ownClosed } = await chrome.storage.session.get("ownClosed");
  if (ownClosed === true) return;
  const origins = await connectedOrigins();
  if (!origins.length) return;
  const tabs = await chrome.tabs.query({});
  if (!tabs.length || tabs.some((t) => OE.onConnected(t.url, origins))) return;
  const url = `${origins[0]}/`;
  const id = await ownWindowId();
  if (id !== null) {
    await chrome.tabs.create({ windowId: id, url, pinned: true, active: false });
    return;
  }
  const win = await makeOwnWindow(url);
  const first = win.tabs && win.tabs[0];
  if (first) await chrome.tabs.update(first.id, { pinned: true });
}

const KEEP_ALARM = "oe-keep-connected";
chrome.alarms.create(KEEP_ALARM, { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === KEEP_ALARM) void keepConnected().catch(() => undefined);
});
chrome.runtime.onStartup.addListener(() => void keepConnected().catch(() => undefined));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.origins || changes.stayConnected)) void keepConnected().catch(() => undefined);
});
// Closed by the person: not opened again until Chrome restarts, or they turn
// "Stay connected" back on.
chrome.windows.onRemoved.addListener((id) => {
  void chrome.storage.session.get("ownWindowId").then(({ ownWindowId: own }) => {
    if (own === id) void chrome.storage.session.set({ ownWindowId: null, ownClosed: true });
  });
});

// ---- Texts (Google Messages) ----
//
// While the person has Texts on in Open Executive (an Open Executive tab's
// relay says so, ``textsOn``), a Google Messages tab stays open: theirs if
// they have one, else one it keeps in its own window, in the "Open Executive"
// tab group so they can find it. texts.js on that
// page says what it shows, which goes on to Open Executive through an Open
// Executive tab, signed in as them; a text Open Executive asks to send is
// typed there.

const MESSAGES = "https://messages.google.com/web/conversations";
const onMessages = (url) => {
  try {
    const u = new URL(String(url || ""));
    return u.protocol === "https:" && u.hostname === "messages.google.com";
  } catch {
    return false;
  }
};

async function messagesTab() {
  const tabs = await chrome.tabs.query({ url: "https://messages.google.com/*" });
  const { textsTabId } = await chrome.storage.session.get("textsTabId");
  return tabs.find((t) => t.id === textsTabId) || tabs[0] || null;
}

async function keepTexts() {
  const { textsOn } = await chrome.storage.local.get("textsOn");
  const { textsTabId } = await chrome.storage.session.get("textsTabId");
  if (textsOn !== true) {
    // Off: forget what Google Messages showed, so on again is a first look.
    await chrome.storage.local.remove(["textsSeen", "textsSeen2"]);
    // Close the tab it opened, never one of theirs.
    if (typeof textsTabId === "number") {
      await chrome.tabs.remove(textsTabId).catch(() => undefined);
      await chrome.storage.session.set({ textsTabId: null });
    }
    return null;
  }
  const have = await messagesTab();
  const id = await ownWindowId();
  if (have) {
    // Its own tab (the one it opened, or the one in its own window, which an
    // update forgets) goes in the "Open Executive" group, so the person can
    // find it and tell what it is. Never one of theirs.
    if ((have.id === textsTabId || (id !== null && have.windowId === id)) && have.groupId === -1) {
      if (have.pinned) await chrome.tabs.update(have.id, { pinned: false }).catch(() => undefined);
      await intoGroup(have).catch(() => undefined);
      await chrome.storage.session.set({ textsTabId: have.id });
    }
    return have;
  }
  let tab;
  if (id !== null) {
    tab = await chrome.tabs.create({ windowId: id, url: MESSAGES, active: false });
  } else {
    // A window of its own only as keepConnected would open one: never once
    // the person closed it or turned staying connected off, and never into
    // a Chrome with no windows open.
    const { stayConnected } = await chrome.storage.local.get("stayConnected");
    const { ownClosed } = await chrome.storage.session.get("ownClosed");
    if (stayConnected === false || ownClosed === true || !(await chrome.tabs.query({})).length) return null;
    const win = await makeOwnWindow(MESSAGES);
    tab = win.tabs && win.tabs[0];
  }
  if (tab) await intoGroup(tab).catch(() => undefined);
  if (tab) await chrome.storage.session.set({ textsTabId: tab.id });
  return tab || null;
}

const TEXTS_ALARM = "oe-keep-texts";
chrome.alarms.create(TEXTS_ALARM, { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === TEXTS_ALARM) void keepTexts().catch(() => undefined);
});
chrome.runtime.onStartup.addListener(() => void keepTexts().catch(() => undefined));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.textsOn) void keepTexts().catch(() => undefined);
});

// What Google Messages shows, passed on through a connected Open Executive tab.
// Whether Texts is on, per connected site (``textsOnBy``), and overall
// (``textsOn``: on while any of them is on). Two sites, one on and one off,
// never turn it on and off in turn. One change at a time.
let textsStateQueue = Promise.resolve();
function setTextsOn(origin, on, origins) {
  const run = textsStateQueue.then(async () => {
    const { textsOnBy, textsOn } = await chrome.storage.local.get(["textsOnBy", "textsOn"]);
    const by = textsOnBy && typeof textsOnBy === "object" ? { ...textsOnBy } : {};
    by[origin] = on;
    for (const known of Object.keys(by)) {
      if (!origins.includes(known)) delete by[known];
    }
    const any = Object.values(by).some((v) => v === true);
    await chrome.storage.local.set(any === textsOn ? { textsOnBy: by } : { textsOnBy: by, textsOn: any });
  });
  textsStateQueue = run.catch(() => undefined);
  return run;
}

async function passOnTexts(body) {
  // Only to sites where Texts is on, and to each of them: one tab per site.
  const origins = await connectedOrigins();
  const { textsOnBy } = await chrome.storage.local.get("textsOnBy");
  const on = new Set(origins.filter((o) => textsOnBy && textsOnBy[o] === true));
  const tabs = (await chrome.tabs.query({})).filter((t) => OE.onConnected(t.url, origins) && on.has(OE.originOf(t.url)));
  const took = new Set();
  for (const tab of tabs) {
    const origin = OE.originOf(tab.url);
    if (took.has(origin)) continue;
    try {
      const out = await chrome.tabs.sendMessage(tab.id, { type: "texts_post", body });
      if (out && out.ok) took.add(origin);
    } catch {
      // That tab's script isn't there yet: try the site's next tab.
    }
  }
  // Every site with Texts on took them, or they go again next time.
  // `nobody`: no site tab was open to take them at all.
  return { ok: on.size > 0 && took.size === on.size, nobody: tabs.length === 0 };
}

// Send one text in Google Messages: open its conversation (or a new one) in
// the Google Messages tab and have texts.js type and send it there.
// One text at a time: every send drives the same Google Messages tab, so a
// second one waits until the first has gone (or failed).
let textQueue = Promise.resolve();
function sendText(send) {
  const run = textQueue.then(() => sendOneText(send));
  textQueue = run.catch(() => undefined);
  return run;
}

// The Google Messages tab that is live: it runs in one tab at a time and
// shows "Use here" in the others. Its own tab first, then any other.
// `allElsewhere`: every tab answered and showed Use here (as after Chrome
// restarts), the only time a tab may take it over. A tab that didn't
// answer (open from before an update, loading, frozen) may be the live one.
// `skipId`: leave that tab out. Right after a tab is opened on a
// conversation its page may not have drawn Use here yet, so its first
// answer can't be trusted; the recheck before a take-over skips it and
// only asks whether some other tab went live.
async function liveMessagesTab(skipId) {
  const tabs = (await chrome.tabs.query({ url: "https://messages.google.com/*" })).filter((t) => t.id !== skipId);
  const { textsTabId } = await chrome.storage.session.get("textsTabId");
  tabs.sort((a, b) => (b.id === textsTabId) - (a.id === textsTabId));
  let allElsewhere = tabs.length > 0;
  for (const t of tabs) {
    const ping = await chrome.tabs.sendMessage(t.id, { type: "texts_ping" }).catch(() => null);
    if (ping && ping.ok && !ping.elsewhere) return { tab: t, allElsewhere: false };
    if (!ping || !ping.ok || ping.elsewhere !== true) allElsewhere = false;
  }
  return { tab: null, allElsewhere };
}

async function sendOneText(send) {
  const text = typeof send.text === "string" ? send.text : "";
  const key = typeof send.conversation === "string" ? send.conversation : "";
  const number = typeof send.number === "string" ? send.number : "";
  if (!text.trim() || (!key && !number)) return { ok: false, error: "Nothing to send, or no one to send it to." };
  const kept = await keepTexts();
  if (!kept) return { ok: false, error: "Google Messages isn't open in Chrome. Nothing went." };
  // Send where Google Messages is live, so a draft in another tab is never
  // taken from under the person. Only with no tab live may this one take over.
  const live = await liveMessagesTab();
  const tab = live.tab || kept;
  const url = key ? `${MESSAGES}/${encodeURIComponent(key)}` : `${MESSAGES}/new`;
  // Google Messages runs in one tab at a time, so this may be the person's
  // own. Never take it from under a half-typed text of theirs.
  const { textsTabId } = await chrome.storage.session.get("textsTabId");
  if (tab.id !== textsTabId) {
    const busy = await chrome.tabs.sendMessage(tab.id, { type: "texts_busy" }).catch(() => null);
    if (busy && busy.busy) return { ok: false, error: "They were typing in Google Messages. Nothing went." };
  }
  await chrome.tabs.update(tab.id, { url });
  await waitLoaded(tab.id);
  // The page script starts after the page loads: ask until it answers.
  let ready = false;
  for (let i = 0; i < 20 && !ready; i++) {
    try {
      const ping = await chrome.tabs.sendMessage(tab.id, { type: "texts_ping" });
      ready = Boolean(ping && ping.ok);
    } catch {
      // Not there yet.
    }
    if (!ready) await new Promise((ok) => setTimeout(ok, 500));
  }
  // Nothing was handed over, so nothing can have gone.
  if (!ready) return { ok: false, error: "Google Messages didn't load. Nothing went." };
  try {
    pressing = { tabId: tab.id, conversation: key };
    const out = await chrome.tabs.sendMessage(tab.id, { type: "texts_send_here", conversation: key, number, text, mayTakeOver: live.allElsewhere && !(await liveMessagesTab(tab.id)).tab });
    return out && typeof out === "object" ? out : { ok: false, unsure: true, error: "Google Messages didn't answer" };
  } catch {
    // Handed over, then no answer: it may have gone.
    return { ok: false, unsure: true, error: "Google Messages stopped answering while sending" };
  } finally {
    pressing = null;
  }
}

// Google Messages sends only on a real press: a click a page script makes
// (element.click(), dispatched mouse or key events) is ignored and the text
// stays in the box. So the press goes through Chrome's own input, by
// attaching to that one tab for the click and detaching straight after.
// Only the tab a send was handed to, only while that send runs, and only on
// the conversation it was handed (or a new one for a number).
let pressing = null;

// Attaching shows Chrome's "is debugging this browser" bar, which makes the
// page shorter and moves the compose bar up. So the Send button is found
// again once attached, after the page has settled, and the click goes where
// it is then. Same order as SEND in texts.js: the first selector with a
// button on screen, and only an enabled one.
const SEND_SPOT = `(() => {
  const box = document.querySelector('[data-e2e-message-input-box], mws-message-compose textarea, mws-message-compose [contenteditable="true"]');
  const full = Boolean(box && String(box.value !== undefined && box.tagName === "TEXTAREA" ? box.value : box.textContent || "").trim());
  for (const sel of ["[data-e2e-send-text-button]", "mws-message-send-button button", 'button[aria-label^="Send" i]']) {
    const shown = [...document.querySelectorAll(sel)].filter((b) => b.getClientRects().length > 0);
    if (!shown.length) continue;
    const b = shown.find((x) => !x.disabled);
    if (!b) return null;
    const r = b.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    const under = document.elementFromPoint(x, y);
    return { x, y, h: innerHeight, hit: Boolean(under && (under === b || b.contains(under))), full, at: location.origin + location.pathname };
  }
  return null;
})()`;

async function readSpot(target) {
  const got = await chrome.debugger.sendCommand(target, "Runtime.evaluate", { expression: SEND_SPOT, returnByValue: true }).catch(() => null);
  return got && got.result ? got.result.value || null : null;
}

const pause = (ms) => new Promise((ok) => setTimeout(ok, ms));

// Where to press, once the page has stopped moving. Attaching makes Chrome
// show its "debugging" bar, which shrinks the page a moment later and moves
// the Send button up: pressing where it was misses it. So wait for the page
// to get shorter (or ~2.5 s, when no bar comes), then for three equal
// readings with the button really under that spot. The page it reads is
// checked in the same reading as the spot it clicks: if the person opened
// another conversation meanwhile, nothing is pressed.
async function sendSpot(target, at, settle = true) {
  const start = await readSpot(target);
  if (!start || start.at !== at) return null;
  if (settle) {
    for (let i = 0; i < 25; i++) {
      await pause(100);
      const now = await readSpot(target);
      if (now && now.at !== at) return null;
      if (now && now.h < start.h) break;
    }
  }
  let last = null;
  let same = 0;
  for (let i = 0; i < 20; i++) {
    const spot = await readSpot(target);
    if (spot && spot.at !== at) return null;
    same = spot && spot.hit && last && spot.x === last.x && spot.y === last.y && spot.h === last.h ? same + 1 : 0;
    if (same >= 2) return spot;
    last = spot;
    await pause(150);
  }
  return null;
}

async function pressAt(target, spot) {
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type,
      x: spot.x,
      y: spot.y,
      button: type === "mouseMoved" ? "none" : "left",
      buttons: type === "mousePressed" ? 1 : 0,
      clickCount: type === "mouseMoved" ? 0 : 1,
      pointerType: "mouse",
    });
  }
}

// Chrome only delivers a press to a page that is on screen. Google Messages
// sits in a background tab of the extension's own window, which is kept
// minimized: there the press goes nowhere, though the button is found. So
// for the press the window is shown (never focused, so what the person types
// stays in their own window) with the tab showing, then put back the way it
// was. If the person went to it meanwhile (its window took the focus), it is
// left where they are.
async function showForPress(tab, target) {
  const win = await chrome.windows.get(tab.windowId, { populate: true }).catch(() => null);
  if (!win) return null;
  const shown = (win.tabs || []).find((t) => t.active) || null;
  const back = { windowId: win.id, minimized: win.state === "minimized", tabId: shown && shown.id !== tab.id ? shown.id : null, focused: win.focused, focusedId: null };
  // Whatever step fails, what was changed is still put back.
  try {
    if (back.minimized) await chrome.windows.update(win.id, { state: "normal", focused: false });
    if (back.tabId !== null) {
      await chrome.tabs.update(tab.id, { active: true });
      // In a window the person is typing in, their keys would now go to the
      // box holding the text: take the focus off it straight away.
      await chrome.debugger.sendCommand(target, "Runtime.evaluate", { expression: UNFOCUS }).catch(() => null);
    }
    await pause(400);
  } catch {
    // Put back by the caller.
  }
  return back;
}

const UNFOCUS = `(() => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur(); })()`;

// Only when the press didn't go from behind their windows: the window comes
// in front for that one press, with its focused field blurred so nothing
// typed elsewhere reaches the box, and the focus goes straight back after it.
async function focusForPress(back, target) {
  const [before] = (await chrome.windows.getAll({ windowTypes: ["normal"] })).filter((w) => w.focused);
  // Only from another Chrome window, which the focus can go back to: never
  // from another app, which it could not.
  if (!before || before.id === back.windowId) return false;
  back.focusedId = before.id;
  back.tookFocus = true;
  // Keys they were typing elsewhere must not reach the box holding the text:
  // blurred before the window takes the focus, and again after in case the
  // page put it back.
  await chrome.debugger.sendCommand(target, "Runtime.evaluate", { expression: UNFOCUS }).catch(() => null);
  await chrome.windows.update(back.windowId, { focused: true });
  await chrome.debugger.sendCommand(target, "Runtime.evaluate", { expression: UNFOCUS }).catch(() => null);
  await pause(200);
  return true;
}

async function putBack(tab, back) {
  if (!back || back.done) return;
  if (back.focused) {
    // Their own window, in front: only their tab comes back, and only if
    // Messages is still the one showing (they didn't move on themselves).
    const now = await chrome.tabs.get(tab.id).catch(() => null);
    if (back.tabId !== null && now && now.active) await chrome.tabs.update(back.tabId, { active: true }).catch(() => undefined);
    back.done = true;
    return;
  }
  const win = await chrome.windows.get(back.windowId).catch(() => null);
  // The person went to it meanwhile: leave it where they are.
  if (!win || (win.focused && !back.tookFocus)) return;
  if (back.tabId !== null) await chrome.tabs.update(back.tabId, { active: true }).catch(() => undefined);
  if (back.minimized) await chrome.windows.update(back.windowId, { state: "minimized" }).catch(() => undefined);
  if (back.focusedId !== null) await chrome.windows.update(back.focusedId, { focused: true }).catch(() => undefined);
  back.done = true;
}

async function pressInTab(tab, x, y) {
  if (!tab || !pressing || tab.id !== pressing.tabId) return { ok: false };
  const conversation = pressing.conversation;
  if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x > 10000 || y > 10000) return { ok: false };
  const now = await chrome.tabs.get(tab.id).catch(() => null);
  if (!now || !onMessages(now.url)) return { ok: false };
  const at = String(now.url).split(/[?#]/)[0];
  if (conversation && !at.endsWith(`/${encodeURIComponent(conversation)}`)) return { ok: false };
  // A new number has no conversation to match: it must still be on the one
  // the send opened, the page it was on when it asked for the press.
  if (!conversation && !/\/web\/conversations\/(?!new$)[^/]+$/.test(at)) return { ok: false };
  const target = { tabId: tab.id };
  try {
    await chrome.debugger.attach(target, "1.3");
  } catch {
    return { ok: false };
  }
  const back = await showForPress(now, target).catch(() => null);
  // Once the press has started it may have gone, even if a step after fails.
  let started = false;
  try {
    const spot = await sendSpot(target, at);
    if (!spot) return { ok: false };
    started = true;
    await pressAt(target, spot);
    // Missed anyway (the page moved under the press): the words are still
    // in the box and the button is somewhere else now. Press once more
    // there. Never when the box emptied, which is what a send does, so a
    // text that went is never sent twice.
    await pause(1500);
    let after = await readSpot(target);
    if (after && after.at === at && after.full && (after.x !== spot.x || after.y !== spot.y)) {
      const again = await sendSpot(target, at, false);
      if (again && again.full) await pressAt(target, again);
      await pause(1500);
      after = await readSpot(target);
    }
    // Still in the box: a window behind the person's own may not take a
    // press. Once, it comes in front for the press alone, and the focus goes
    // straight back, so nothing they type lands in the box.
    // The spot is found before the focus is taken, and the focus is held
    // only for the press, with the box unfocused.
    if (back && !back.focused && after && after.at === at && after.full) {
      const last = await sendSpot(target, at, false);
      if (last && last.full) {
        if (await focusForPress(back, target)) {
          const check = await readSpot(target);
          if (check && check.at === at && check.full && check.x === last.x && check.y === last.y) await pressAt(target, check);
          await putBack(now, back);
        }
      }
    }
    return { ok: true };
  } catch {
    return { ok: false, maybe: started };
  } finally {
    await chrome.debugger.detach(target).catch(() => undefined);
    await putBack(now, back).catch(() => undefined);
  }
}

function waitLoaded(tabId, ms = 20000) {
  return new Promise((done) => {
    const timer = setTimeout(finish, ms);
    function finish() {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listen);
      done();
    }
    function listen(id, info) {
      if (id === tabId && info.status === "complete") finish();
    }
    chrome.tabs.onUpdated.addListener(listen);
    chrome.tabs.get(tabId).then((t) => t.status === "complete" && finish(), finish);
  });
}

// Tabs it opens go in one "Open Executive" group per window, so the person
// can tell them from their own. Never their own tabs. Skipped where tab
// groups aren't there.
const GROUP_TITLE = "Open Executive";

async function intoGroup(tab) {
  if (!chrome.tabGroups || !tab || typeof tab.id !== "number") return;
  const [group] = await chrome.tabGroups.query({ windowId: tab.windowId, title: GROUP_TITLE });
  if (group) {
    await chrome.tabs.group({ tabIds: [tab.id], groupId: group.id });
    return;
  }
  const groupId = await chrome.tabs.group({ tabIds: [tab.id], createProperties: { windowId: tab.windowId } });
  await chrome.tabGroups.update(groupId, { title: GROUP_TITLE, color: "blue" });
}

async function sendToPage(tabId, msg) {
  try {
    return await chrome.tabs.sendMessage(tabId, msg);
  } catch {
    // A tab opened before the extension was added has no page script yet.
    await chrome.scripting.executeScript({ target: { tabId }, files: ["site-rules.js", "shared.js", "content.js"] });
    return await chrome.tabs.sendMessage(tabId, msg);
  }
}

// ---- The screenshot with each look ----

// At most this wide (CSS pixels), and small enough to send.
const SHOT_WIDTH = 1280;
const SHOT_MAX_CHARS = 300000;
// The last screenshot's size per tab, so a click on it lands on the page:
// { [tabId]: { url, width, height, cssWidth, cssHeight } }.
let shots = {};
const shotsRestored = chrome.storage.session
  .get("shots")
  .then(({ shots: saved }) => {
    if (saved && typeof saved === "object") shots = { ...saved, ...shots };
  })
  .catch(() => undefined);

// A page view (size and scroll) as one comparable string, or "" when unknown.
function sameView(view) {
  if (!view || typeof view !== "object") return "";
  const n = ["width", "height", "x", "y"].map((k) => Math.round(Number(view[k])));
  return n.every(Number.isFinite) ? n.join(",") : "";
}

function forgetShot(tabId) {
  if (!(tabId in shots)) return;
  delete shots[tabId];
  void chrome.storage.session.set({ shots });
}
chrome.tabs.onRemoved.addListener((tabId) => {
  void shotsRestored.then(() => forgetShot(tabId));
});

function base64(buffer) {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < bytes.length; i += 0x8000) out += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(out);
}

// What is on screen in `tab` (the active tab of its window), shrunk to at
// most SHOT_WIDTH CSS pixels wide, as JPEG. The Stop bar is hidden for it.
// Whether `tab` is still the one on screen in its window, at the same address.
async function stillShown(tab) {
  const now = await chrome.tabs.get(tab.id).catch(() => null);
  return !!now && now.active && now.url === tab.url && now.windowId === tab.windowId;
}

// A screenshot of its own window, for an ask made away from this computer
// (`shot`): the
// window is kept minimized, and a minimized window has nothing on screen to
// capture, so it is shown behind the person's own windows (never focused)
// just for the capture, then minimized again.
async function ownScreenshot(tab, view) {
  const win = await chrome.windows.get(tab.windowId).catch(() => null);
  if (!win || win.state !== "minimized") return await screenshot(tab, view);
  await chrome.windows.update(win.id, { state: "normal", focused: false });
  try {
    await new Promise((done) => setTimeout(done, 400));
    return await screenshot(tab, view);
  } finally {
    // Put it back only if the person didn't open it meanwhile.
    const now = await chrome.windows.get(win.id).catch(() => null);
    if (now && now.state === "normal" && !now.focused) {
      await chrome.windows.update(win.id, { state: "minimized" }).catch(() => undefined);
    }
  }
}

async function screenshot(tab, view) {
  const snap = await sendToPage(tab.id, { type: "snap", hide: true }).catch(() => null);
  // No word from the page means no masks for its password, card and code
  // fields: no picture then.
  if (!snap || !Array.isArray(snap.masks)) {
    void sendToPage(tab.id, { type: "snap", hide: false }).catch(() => null);
    return null;
  }
  // Where the page was when it was taken, so a click on it can tell the page moved.
  const at = snap && typeof snap.view === "object" ? snap.view : view;
  let url;
  try {
    // The capture takes whatever tab is on screen: only this one, never
    // another the person switched to meanwhile.
    if (!(await stillShown(tab))) return null;
    url = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 80 });
    if (!(await stillShown(tab))) return null;
  } finally {
    void sendToPage(tab.id, { type: "snap", hide: false }).catch(() => null);
  }
  const bitmap = await createImageBitmap(await (await fetch(url)).blob());
  const cssWidth = Math.max(1, Math.round(Number(view?.width) || bitmap.width));
  const cssHeight = Math.max(1, Math.round(Number(view?.height) || bitmap.height));
  const width = Math.min(SHOT_WIDTH, cssWidth);
  const height = Math.max(1, Math.round((bitmap.height * width) / bitmap.width));
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  // Password, card and code fields are blacked out: what they typed there
  // never reaches the Executive.
  ctx.fillStyle = "#000";
  for (const box of OE.scaledMasks(snap.masks, width / cssWidth)) ctx.fillRect(box.x, box.y, box.w, box.h);
  let data = "";
  for (const quality of [0.6, 0.4, 0.25]) {
    data = base64(await (await canvas.convertToBlob({ type: "image/jpeg", quality })).arrayBuffer());
    if (data.length <= SHOT_MAX_CHARS) break;
  }
  if (data.length > SHOT_MAX_CHARS) return null;
  shots[tab.id] = { url: tab.url, width, height, cssWidth, cssHeight, at: sameView(at) };
  void chrome.storage.session.set({ shots });
  return { media_type: "image/jpeg", data, width, height };
}

// A point on the last screenshot of `tab`, as a point on its page, or why not.
async function pagePoint(tab, args) {
  await shotsRestored;
  const shot = shots[tab.id];
  const stale = { error: "Look at the page again first (browser_look): click_at goes by the latest screenshot, and the page may have changed since." };
  if (!shot || shot.url !== tab.url || !shot.at) return stale;
  // Scrolled or resized since (by the person too): the pixels no longer match.
  const now = await sendToPage(tab.id, { type: "view" }).catch(() => null);
  if (!now || sameView(now.view) !== shot.at) {
    forgetShot(tab.id);
    return stale;
  }
  const x = Number(args.x);
  const y = Number(args.y);
  if (!(x >= 0 && x < shot.width && y >= 0 && y < shot.height)) {
    return { error: `That point is off the screenshot (it is ${shot.width}x${shot.height}).` };
  }
  return { cx: (x * shot.cssWidth) / shot.width, cy: (y * shot.cssHeight) / shot.height };
}

const NOT_HERE =
  "That's Open Executive itself: there I can open pages (browser_show_page), look and point things out, but they press its buttons and type there. Point to what to press.";

// Steps that only read or show, so they may run on Open Executive itself.
const SHOWS = new Set(["look", "scroll", "point"]);

// Open an Open Executive page in the Open Executive tab the person is
// working in, in the page (so it never reloads). Whichever of their
// Open Executive tabs took the step (the extension's own window keeps one for
// workflows), it never brings up another window, unless they asked for a new
// one. With no Open Executive tab of theirs, it opens as a new tab in their
// window's Open Executive group.
async function showPage(relayTab, path, newWindow = false) {
  const where = OE.pagePath(path);
  if (!where) return { ok: false, error: "That isn't an Open Executive page path. Use a path like /knowledge." };
  if (!relayTab) return { ok: false, error: "The Open Executive tab isn't open." };
  const origin = OE.originOf(relayTab.url);
  const win = await personWindow();
  if (newWindow || !win) {
    // Asked for, or no window of theirs is open.
    const tabId = await newWindowTab(`${origin}${where}`);
    await waitLoaded(tabId);
    return { ok: true, done: true, path: where };
  }
  const tab = OE.workingTab(await chrome.tabs.query({}), origin, {
    ownWindowId: await ownWindowId(),
    focusedWindowId: win ? win.id : null,
  });
  if (!tab) {
    const made = await chrome.tabs.create({ url: `${origin}${where}`, active: true, windowId: win.id });
    await intoGroup(made).catch(() => undefined);
    await chrome.windows.update(win.id, { focused: true });
    await waitLoaded(made.id);
    return { ok: true, done: true, path: where };
  }
  await chrome.tabs.update(tab.id, { active: true });
  await chrome.windows.update(tab.windowId, { focused: true });
  const out = await sendToPage(tab.id, { type: "go", path: where });
  return out && typeof out === "object" ? out : { ok: false, error: "Open Executive didn't answer. Give them the link." };
}

// ---- Waiting on you: steps handed over to the person ----

// The open hand-overs, by tab: { [tabId]: { id, need, origin, start, signIn } }.
// `origin` is the Open Executive site to tell; `signIn` says the page was a
// sign-in form, so leaving it says Done by itself.
async function handovers() {
  const { handovers: saved } = await chrome.storage.session.get("handovers");
  return saved && typeof saved === "object" ? saved : {};
}

async function saveHandovers(all) {
  await chrome.storage.session.set({ handovers: all });
}

async function handOver(args, relayTab, own, side, origins) {
  const id = String(args.id || "").slice(0, 64);
  const need = OE.squeeze(String(args.need || ""), 140);
  if (!/^[0-9a-f]{8,64}$/.test(id) || !need) return { ok: false, error: "Say what they need to do." };
  if (!relayTab) return { ok: false, error: "The Open Executive tab isn't open." };
  const tab = await targetTab(own, side);
  if (!tab) return { ok: false, error: "No page is open to hand over. Open it first." };
  if (OE.onConnected(tab.url, origins)) {
    return { ok: false, error: "That's Open Executive itself: tell them in chat what to do there." };
  }
  if (OE.blockedUrl(tab.url)) return { ok: false, error: "That page can't be used by the extension." };
  // In front of them: un-minimized (a workflow's own window), focused.
  const win = await chrome.windows.get(tab.windowId);
  if (win.state === "minimized") await chrome.windows.update(win.id, { state: "normal" });
  await chrome.tabs.update(tab.id, { active: true });
  await chrome.windows.update(tab.windowId, { focused: true });
  const form = await sendToPage(tab.id, { type: "signin" }).catch(() => null);
  const all = await handovers();
  all[tab.id] = {
    id,
    need,
    origin: OE.originOf(relayTab.url),
    start: String(tab.url || ""),
    signIn: Boolean(form && form.signIn),
  };
  await saveHandovers(all);
  await sendToPage(tab.id, { type: "handover_show", id, need }).catch(() => undefined);
  // The server takes the page's own Done only for a sign-in hand-over.
  return { ok: true, done: true, url: tab.url, title: tab.title, sign_in: all[tab.id].signIn };
}

// Done or Stop: the bar comes down and the Open Executive site hears it,
// through one of its tabs (it's signed in there).
async function finishHandover(tabId, outcome) {
  const all = await handovers();
  const entry = all[tabId];
  if (!entry) return { ok: true, gone: true };
  const tabs = await chrome.tabs.query({});
  const site = tabs.find((t) => entry.origin && OE.originOf(t.url) === entry.origin && OE.onConnected(t.url, [entry.origin]));
  if (!site) return { ok: false, error: "Open an Open Executive tab, then press it again." };
  const out = await sendToPage(site.id, { type: "handover_post", id: entry.id, outcome }).catch(() => null);
  if (!out || !out.ok) return { ok: false, error: (out && out.error) || "Open Executive didn't answer. Try again." };
  // Its own Done (a sign-in left) when nothing waits any more: the bar stays.
  if (out.kept) return { ok: true, kept: true };
  const now = await handovers();
  delete now[tabId];
  await saveHandovers(now);
  await chrome.tabs.sendMessage(tabId, { type: "handover_hide" }).catch(() => undefined);
  return { ok: true, carried_on: out.carried_on === true, on_card: out.on_card === true };
}

// Said Done or Stop somewhere else (its card): just take the bar down.
async function endHandover(id) {
  const all = await handovers();
  const tabId = Object.keys(all).find((k) => all[k].id === id);
  if (tabId === undefined) return { ok: true, done: true };
  delete all[tabId];
  await saveHandovers(all);
  await chrome.tabs.sendMessage(Number(tabId), { type: "handover_hide" }).catch(() => undefined);
  return { ok: true, done: true };
}

// A page loaded in a handed-over tab: the bar again, and Done by itself once
// a sign-in page has been left for one with no sign-in form.
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status !== "complete") return;
  void (async () => {
    const entry = (await handovers())[tabId];
    if (!entry) return;
    if (entry.signIn) {
      // A moment for the page to draw its form.
      await new Promise((ok) => setTimeout(ok, 1500));
      const form = await sendToPage(tabId, { type: "signin" }).catch(() => null);
      const now = await chrome.tabs.get(tabId).catch(() => tab);
      if (form && OE.pastSignIn(entry.start, now.url, Boolean(form.signIn))) {
        // Only a hint from the page: the server takes it only while the
        // Executive is still waiting for it.
        const out = await finishHandover(tabId, "auto");
        if (out.ok && !out.kept) return;
      }
    }
    if ((await handovers())[tabId]) {
      await sendToPage(tabId, { type: "handover_show", id: entry.id, need: entry.need }).catch(() => undefined);
    }
  })().catch(() => undefined);
});

// A handed-over tab closed: that's a Stop.
chrome.tabs.onRemoved.addListener((tabId) => {
  void handovers()
    .then((all) => (all[tabId] ? finishHandover(tabId, "stop") : null))
    .then(async () => {
      const all = await handovers();
      if (all[tabId]) {
        delete all[tabId];
        await saveHandovers(all);
      }
    })
    .catch(() => undefined);
});

async function run(step, relayTab, late = { on: false }) {
  const { action } = step;
  let args = step.args || {};
  await restored;
  if (Date.now() < stoppedUntil) {
    return { ok: false, error: "They pressed Stop. Don't do more in their browser unless they ask again." };
  }
  const origins = await connectedOrigins();
  const own = args.window === "own";
  const side = !own && args.window === "side";
  const NO_PAGE = "Nothing is open in its own window yet. Open a page first (browser_open_tab).";
  if (action === "hand_over") return await handOver(args, relayTab, own, side, origins);
  if (action === "hand_over_end") return await endHandover(String(args.id || ""));
  if (action === "tabs") {
    const tab = await targetTab(own, side);
    if (!tab) return { ok: false, error: own ? NO_PAGE : "No browser window is open." };
    let tabs = await chrome.tabs.query({ windowId: tab.windowId });
    if (own) tabs = tabs.filter((t) => !t.pinned && !onMessages(t.url) && !OE.onConnected(t.url, origins));
    return {
      ok: true,
      tabs: tabs.map((t) => ({
        id: t.id,
        title: OE.squeeze(t.title, 100),
        url: OE.squeeze(t.url, 200),
        active: t.active,
        open_executive: OE.onConnected(t.url, origins),
      })),
    };
  }
  if (action === "switch_tab") {
    const tab = await chrome.tabs.get(Number(args.tab_id)).catch(() => null);
    if (!tab) return { ok: false, error: "No tab with that id. List the tabs again." };
    if (own) {
      // Only its own window's tabs, and the person's view stays where it is.
      if (tab.windowId !== (await ownWindowId()) || tab.pinned || onMessages(tab.url)) {
        return { ok: false, error: "No tab with that id. List the tabs again." };
      }
      await chrome.tabs.update(tab.id, { active: true });
      return { ok: true, done: true, url: tab.url, title: tab.title };
    }
    await chrome.tabs.update(tab.id, { active: true });
    // In the window beside the chat, the chat keeps the focus.
    if (!(side && tab.windowId === (await sideWindowId()))) await chrome.windows.update(tab.windowId, { focused: true });
    return { ok: true, done: true, url: tab.url, title: tab.title };
  }
  if (action === "show_page") {
    if (own) return { ok: false, error: "Open Executive's own pages can't be opened in its own window." };
    return await showPage(relayTab, args.path, args.new_window === true);
  }
  if (own && (action === "open_tab" || action === "navigate")) {
    if (!OE.openableUrl(args.url) || OE.onConnected(args.url, origins)) {
      return { ok: false, error: "That address can't be opened from here." };
    }
    if (args.hold === true) {
      return { ok: false, held: true, url: String(args.url).trim().slice(0, 2048) };
    }
    const current = await ownTab();
    let tabId;
    if (action === "open_tab" || !current) {
      const id = await ownWindowId();
      if (id === null) {
        const win = await makeOwnWindow(args.url.trim());
        tabId = win.tabs[0].id;
      } else {
        tabId = (await chrome.tabs.create({ url: args.url.trim(), active: true, windowId: id })).id;
      }
      await intoGroup(await chrome.tabs.get(tabId)).catch(() => undefined);
    } else {
      await chrome.tabs.update(current.id, { url: args.url.trim() });
      tabId = current.id;
    }
    await waitLoaded(tabId);
    const tab = await chrome.tabs.get(tabId);
    return { ok: true, done: true, url: tab.url, title: tab.title };
  }
  if ((action === "open_tab" || action === "navigate") && relayTab && OE.onConnected(args.url, origins)) {
    // Its own pages open in its own tab, never a second copy of it.
    const u = new URL(String(args.url).trim());
    if (u.origin !== OE.originOf(relayTab.url)) return { ok: false, error: "That address can't be opened from here." };
    return await showPage(relayTab, u.pathname + u.search + u.hash, args.new_window === true);
  }
  if (action === "open_tab" || action === "navigate") {
    if (!OE.openableUrl(args.url) || OE.onConnected(args.url, origins)) {
      return { ok: false, error: "That address can't be opened from here." };
    }
    if (side && args.new_window !== true) {
      // From a chat: in the window beside it, never over the chat.
      const tabId = await oneAtATime(async () => {
        const there = await sideTab();
        if (!there) {
          const win = await makeSideWindow(args.url.trim());
          const id = win.tabs[0].id;
          await intoGroup(await chrome.tabs.get(id)).catch(() => undefined);
          return id;
        }
        if (action === "open_tab") {
          const made = await chrome.tabs.create({ url: args.url.trim(), active: true, windowId: there.windowId });
          await intoGroup(made).catch(() => undefined);
          return made.id;
        }
        await chrome.tabs.update(there.id, { url: args.url.trim() });
        return there.id;
      });
      await waitLoaded(tabId);
      const tab = await chrome.tabs.get(tabId);
      return { ok: true, done: true, url: tab.url, title: tab.title };
    }
    const current = await targetTab();
    let tabId;
    if (!current || (action === "open_tab" && args.new_window === true)) {
      // Asked for, or no window of theirs is open.
      tabId = await newWindowTab(args.url.trim());
    } else if (action === "open_tab") {
      // A new tab in the window they are in, in its Open Executive group.
      const made = await chrome.tabs.create({ url: args.url.trim(), active: true, windowId: current.windowId });
      tabId = made.id;
      await intoGroup(made).catch(() => undefined);
    } else {
      if (OE.onConnected(current.url, origins)) return { ok: false, error: NOT_HERE };
      await chrome.tabs.update(current.id, { url: args.url.trim() });
      tabId = current.id;
    }
    await waitLoaded(tabId);
    const tab = await chrome.tabs.get(tabId);
    return { ok: true, done: true, url: tab.url, title: tab.title };
  }
  const tab = await targetTab(own, side);
  if (!tab) return { ok: false, error: own ? NO_PAGE : "No browser window is open." };
  if (OE.onConnected(tab.url, origins)) {
    // Only its own site, the one asking: never another connected company's.
    if (!SHOWS.has(action) || !relayTab || OE.originOf(tab.url) !== OE.originOf(relayTab.url)) {
      return { ok: false, error: NOT_HERE };
    }
  }
  if (OE.blockedUrl(tab.url)) {
    return { ok: false, error: "That page can't be used by the extension (a browser page or an extension store)." };
  }
  if (action === "click_at") {
    const at = await pagePoint(tab, args);
    if (at.error) return { ok: false, error: at.error };
    args = { ...args, cx: at.cx, cy: at.cy };
  }
  if (!SHOWS.has(action) && args.hold === true) {
    // Waits for their OK first (a workflow's step): only name what it would
    // act on, for the question they are asked. Nothing is clicked or typed.
    const named = await sendToPage(tab.id, { type: "name", action, args }).catch(() => null);
    const held = { ok: false, held: true, url: String(tab.url || "").slice(0, 2048) };
    if (named && typeof named.element === "string") held.element = named.element.slice(0, 200);
    return held;
  }
  if (!SHOWS.has(action)) {
    const stop = OE.sendingStop(tab.url, args);
    if (stop && !stop.sending_site) return stop;
    if (stop) {
      // Name what it would have acted on, for the card it waits on. The
      // page only names it: nothing is clicked or typed.
      const named = await sendToPage(tab.id, { type: "name", action, args }).catch(() => null);
      if (named && typeof named.element === "string") stop.element = named.element.slice(0, 200);
      return stop;
    }
  }
  if (action !== "look" && action !== "point") {
    // Anything that can move or change the page makes the last screenshot
    // stale: a click on it needs a new look first.
    await shotsRestored;
    forgetShot(tab.id);
  }
  // Answered "didn't finish" already: the Executive has moved on, so a step
  // that got this far late does nothing on the page.
  if (late.on) return { ok: false, error: "Too late." };
  const out = await sendToPage(tab.id, { type: "act", action, args });
  if (!out || typeof out !== "object") return { ok: false, error: "The page didn't answer. Look again." };
  if (action === "look" && out.ok) {
    // What is on screen, beside the text: never stops the look.
    const shot = await (own && args.shot === true ? ownScreenshot(tab, out.view) : screenshot(tab, out.view)).catch(
      () => null,
    );
    if (shot) out.screenshot = shot;
  }
  return out;
}

// Every step answers within this long, even one stuck in Chrome, so the tab
// asking for steps goes on asking (the server waits a little longer).
const STEP_MS = 40000;

// `start(late)` runs the step; once it has answered "didn't finish", `late.on`
// tells it not to act on the page any more.
function withinStepTime(start) {
  const late = { on: false };
  let timer;
  const cut = new Promise((done) => {
    timer = setTimeout(() => {
      late.on = true;
      done({ ok: false, error: "The browser didn't finish that step in time. Look at the page again before going on." });
    }, STEP_MS);
  });
  return Promise.race([start(late), cut]).finally(() => clearTimeout(timer));
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || typeof msg !== "object") return false;
  if (sender.id !== chrome.runtime.id) return false;
  if (msg.type === "stop") {
    stoppedUntil = Date.now() + OE.STOP_MS;
    void chrome.storage.session.set({ stoppedUntil });
    return false;
  }
  if (msg.type === "step") {
    // Only a connected Open Executive tab's relay hands out steps.
    (async () => {
      const origins = await connectedOrigins();
      if (!sender.tab || !OE.onConnected(sender.url || sender.tab.url, origins)) {
        return { ok: false, error: "Not from a connected Open Executive site." };
      }
      try {
        return await withinStepTime((late) => run(msg.step || {}, sender.tab, late));
      } catch (e) {
        return { ok: false, error: `The browser couldn't do that (${String(e && e.message ? e.message : e).slice(0, 120)}).` };
      }
    })().then(reply);
    return true;
  }
  if (msg.type === "texts_state") {
    // Each connected Open Executive site says whether Texts is on there.
    (async () => {
      const origins = await connectedOrigins();
      const url = sender.url || (sender.tab && sender.tab.url);
      if (!sender.tab || !OE.onConnected(url, origins)) return { ok: false };
      await setTextsOn(OE.originOf(url), msg.on === true, origins);
      return { ok: true };
    })().then(reply, () => reply({ ok: false }));
    return true;
  }
  if (msg.type === "texts_press") {
    // Only the Google Messages tab a send is running in asks for its press.
    if (!sender.tab || !onMessages(sender.url || sender.tab.url)) return false;
    pressInTab(sender.tab, Number(msg.x), Number(msg.y))
      .then(reply, () => reply({ ok: false }));
    return true;
  }
  if (msg.type === "texts_seen") {
    // Only the Google Messages page says what Google Messages shows.
    if (!sender.tab || !onMessages(sender.url || sender.tab.url)) return false;
    const away = msg.elsewhere === true;
    const body = {
      paired: !away && msg.paired === true,
      items: !away && Array.isArray(msg.items) ? msg.items.slice(0, 100) : [],
    };
    // Nothing leaves the browser while Texts is off. A tab showing Use here
    // speaks only when no tab here is live, so it never undoes a live one.
    chrome.storage.local
      .get("textsOn")
      .then(async (got) => {
        if (got.textsOn !== true) return { ok: false, off: true };
        if (away && (await liveMessagesTab()).tab) return { ok: true };
        return passOnTexts(body);
      })
      .then(reply, () => reply({ ok: false }));
    return true;
  }
  if (msg.type === "text_send") {
    // Only a connected Open Executive tab hands out texts to send.
    (async () => {
      const origins = await connectedOrigins();
      if (!sender.tab || !OE.onConnected(sender.url || sender.tab.url, origins)) {
        return { ok: false, error: "Not from a connected Open Executive site." };
      }
      if (Date.now() < stoppedUntil) return { ok: false, error: "The person pressed Stop in their browser." };
      return await sendText(msg.send || {});
    })().then(reply, (e) => reply({ ok: false, error: String(e && e.message ? e.message : e).slice(0, 200) }));
    return true;
  }
  if (msg.type === "handover") {
    // Done or Stop on a hand-over's bar: only from the tab it was handed over in.
    (async () => {
      if (!sender.tab) return { ok: false };
      const entry = (await handovers())[sender.tab.id];
      if (!entry || entry.id !== msg.id) return { ok: true, gone: true };
      return await finishHandover(sender.tab.id, msg.outcome === "stop" ? "stop" : "done");
    })().then(reply, () => reply({ ok: false }));
    return true;
  }
  // Resume comes only from the popup (no tab); Stop from any of its pages.
  if (msg.type === "resume" && !sender.tab) {
    stoppedUntil = 0;
    void chrome.storage.session.set({ stoppedUntil: 0 });
    return false;
  }
  return false;
});

