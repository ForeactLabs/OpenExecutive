// Open Executive browser extension: the script on Google Messages.
//
// Google Messages for web (messages.google.com) shows the texts on the
// person's Android phone once they pair it, and sends from that phone. When
// they turn on Texts in Open Executive, the background keeps this page open,
// pinned in the extension's own window. This script:
//
// - tells the background what the page shows: whether it is paired, and each
//   conversation whose latest text changed since it last said (the
//   background passes it on to Open Executive, signed in as the person);
// - types and sends a text the background asks for, in a conversation or to
//   a new number, and says whether it went.
//
// It reads only the conversation list and the conversation it was asked to
// send in, and it sends only what the background hands it (a text the
// person's Approvals let go). Plain script (no modules).

(function () {
  if (window.top !== window) return;
  if (globalThis.__oeTextsLoaded) return;
  globalThis.__oeTextsLoaded = true;

  // ---- What the page shows (kept apart from the page so it can be tested) ----

  const T = {};

  /** A short, stable fingerprint of a text. */
  T.hash = function (text) {
    let h = 5381;
    const s = String(text || "");
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36);
  };

  /** Whether a conversation's title is a phone number rather than a name. */
  T.looksLikeNumber = function (title) {
    const bare = String(title || "").replace(/[\s()+\-.]/g, "");
    return bare.length >= 3 && /^\d+$/.test(bare);
  };

  /** One line of text, at most `max` characters. */
  T.squeeze = function (text, max) {
    return String(text || "").replace(/\s+/g, " ").trim().slice(0, max);
  };

  // The person's own last text starts with this in the list.
  // The server also knows its own sends by their words, whatever the language.
  const YOU = /^(You|Vous|Tú|Tu|Du|Jij|Je|U|Você|Ty|Ви|Вы|Ти|Sen|Siz|Anda|Kamu|Bạn|Eu|Io|Jeg|Jag|Minä|Sinä|자신|나|あなた|自分|我|你)\s*[:：]\s*/i;

  /**
   * A conversation in the list as plain data, or null:
   * { key, title, snippet, unread, outgoing, group }.
   */
  T.fromRow = function (row) {
    if (!row || typeof row.key !== "string" || !row.key || row.key === "new") return null;
    const title = T.squeeze(row.title, 200);
    let snippet = T.squeeze(row.snippet, 4000);
    const outgoing = YOU.test(snippet);
    if (outgoing) snippet = snippet.replace(YOU, "");
    return {
      key: row.key.slice(0, 300),
      title,
      snippet,
      unread: Boolean(row.unread),
      outgoing,
      group: Boolean(row.group) || /,\s/.test(title),
    };
  };

  /**
   * What to tell Open Executive about the list, given what it said before
   * (`seen`: key -> fingerprint of the latest text). The first time (`seen`
   * is null) only unread conversations count as new, so texts from before
   * Texts was on aren't taken as new. Returns { items, seen }.
   */
  T.changes = function (rows, seen, now) {
    const first = seen === null || typeof seen !== "object";
    const next = first ? {} : { ...seen };
    const items = [];
    for (const raw of rows) {
      const row = T.fromRow(raw);
      if (!row || !row.snippet) continue;
      const print = T.hash(`${row.outgoing ? "1" : "0"}:${row.snippet}`);
      const before = first ? null : next[row.key];
      next[row.key] = print;
      if (before === print) continue;
      if (first && (!row.unread || row.outgoing)) continue;
      items.push({
        // The time too: the same words again later ("ok") are a new text.
        ref: `${row.key}:${print}:${Number(now).toString(36)}`,
        conversation: row.key,
        title: row.title,
        sender: row.outgoing || row.group ? "" : row.title,
        text: row.snippet,
        at: new Date(now).toISOString(),
        number: T.looksLikeNumber(row.title) ? row.title.slice(0, 40) : "",
        is_group: row.group,
        outgoing: row.outgoing,
      });
    }
    // Forget conversations far down the list, so the map stays small.
    const keys = Object.keys(next);
    if (keys.length > 500) for (const k of keys.slice(0, keys.length - 500)) delete next[k];
    return { items: items.slice(0, 100), seen: next };
  };

  /** The conversation key in a Google Messages address, or "". */
  T.keyOf = function (href) {
    const m = /\/web\/conversations\/([^/?#]+)/.exec(String(href || ""));
    return m && m[1] !== "new" ? decodeURIComponent(m[1]) : "";
  };

  globalThis.OE_TEXTS = T;
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) return;
  if (location.hostname !== "messages.google.com") return;

  // ---- Reading the page ----

  const first = (root, selectors) => {
    for (const s of selectors) {
      const el = root.querySelector(s);
      if (el) return el;
    }
    return null;
  };
  const textOf = (el) => (el ? el.innerText || el.textContent || "" : "");

  function listRows() {
    const links = document.querySelectorAll('a[href*="/web/conversations/"]');
    const rows = [];
    for (const a of links) {
      const key = T.keyOf(a.getAttribute("href"));
      if (!key) continue;
      const item = a.closest("mws-conversation-list-item") || a;
      rows.push({
        key,
        title: textOf(first(item, ["[data-e2e-conversation-name]", ".name", "h2", "h3"])),
        snippet: textOf(first(item, ["[data-e2e-conversation-snippet]", "mws-conversation-snippet", ".snippet-text"])),
        unread:
          a.classList.contains("unread") ||
          item.classList.contains("unread") ||
          item.getAttribute("data-e2e-is-unread") === "true" ||
          a.getAttribute("data-e2e-is-unread") === "true",
        group: item.getAttribute("data-e2e-is-group") === "true",
      });
    }
    return rows;
  }

  function paired() {
    if (/\/web\/(authentication|welcome)/.test(location.pathname)) return false;
    return Boolean(document.querySelector('mws-conversations-list, a[href*="/web/conversations/"]'));
  }

  // ---- Telling the background ----

  const SEEN_KEY = "textsSeen";
  let reporting = false;
  let lastReport = 0;

  async function report(force) {
    if (reporting) return;
    reporting = true;
    try {
      // Off: read nothing at all, and forget what was seen, so turning it
      // back on is a first look again (only unread texts count as new).
      if ((await chrome.storage.local.get("textsOn")).textsOn !== true) {
        await chrome.storage.local.remove(SEEN_KEY);
        return;
      }
      const now = Date.now();
      const isPaired = paired();
      let items = [];
      if (isPaired) {
        const stored = (await chrome.storage.local.get(SEEN_KEY))[SEEN_KEY];
        const out = T.changes(listRows(), stored && typeof stored === "object" ? stored : null, now);
        items = out.items;
        await chrome.storage.local.set({ [SEEN_KEY]: out.seen });
      }
      // Nothing new: still say it's here, once a minute.
      if (!items.length && !force && now - lastReport < 60000) return;
      lastReport = now;
      await chrome.runtime.sendMessage({ type: "texts_seen", paired: isPaired, items }).catch(() => undefined);
    } finally {
      reporting = false;
    }
  }

  let timer = null;
  const soon = () => {
    clearTimeout(timer);
    timer = setTimeout(() => void report(false), 1500);
  };
  new MutationObserver(soon).observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  setInterval(() => void report(false), 15000);
  setTimeout(() => void report(true), 3000);

  // ---- Sending ----

  const pause = (ms) => new Promise((ok) => setTimeout(ok, ms));

  async function waitFor(find, ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const found = find();
      if (found) return found;
      await pause(250);
    }
    return null;
  }

  function setValue(el, text) {
    el.focus();
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, text);
    else el.value = text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function pressEnter(el) {
    for (const type of ["keydown", "keypress", "keyup"]) {
      el.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
    }
  }

  const COMPOSE = [
    "textarea[data-e2e-message-input-box]",
    "mws-message-compose textarea",
    "mws-autosize-textarea textarea",
    "textarea.input",
    "textarea",
  ];
  const SEND = [
    "[data-e2e-send-text-button]",
    "mws-message-send-button button",
    'button[aria-label^="Send" i]',
  ];

  function outgoingBubbles() {
    return [
      ...document.querySelectorAll(
        'mws-message-wrapper[is-outgoing="true"], [data-e2e-is-outgoing="true"], .outgoing mws-text-message-part',
      ),
    ];
  }
  const outgoingTexts = () => outgoingBubbles().map((el) => T.squeeze(textOf(el), 4000));

  async function startConversation(number) {
    const input = await waitFor(
      () => first(document, [
        "input[data-e2e-contact-input]",
        "mw-contact-chips-input input",
      ]),
      15000,
    );
    if (!input) return "Google Messages didn't show where to type the number.";
    setValue(input, number);
    // Google Messages offers "Send to <number>" once it reads a number.
    // Only the "Send to <number>" choice, showing that very number: never a
    // contact suggestion or Enter, either of which can be someone else.
    const want = String(number).replace(/\D/g, "");
    const pick = await waitFor(() => {
      const el = first(document, ["[data-e2e-send-to-button]", "mw-contact-selector-button button"]);
      const shown = el ? textOf(el.closest("mw-contact-selector-button") || el).replace(/\D/g, "") : "";
      return el && want && shown.includes(want) ? el : null;
    }, 6000);
    if (!pick) return "Google Messages didn't offer to send to that number. Nothing went.";
    pick.click();
    const opened = await waitFor(() => T.keyOf(location.href), 15000);
    return opened ? "" : "Google Messages didn't open a conversation with that number.";
  }

  async function send(msg) {
    const text = String(msg.text || "");
    if (!text.trim()) return { ok: false, error: "There was nothing to send." };
    if (!paired()) return { ok: false, error: "Google Messages isn't paired with their phone." };
    if (!msg.conversation) {
      const problem = await startConversation(String(msg.number || ""));
      if (problem) return { ok: false, error: problem };
    } else if (T.keyOf(location.href) !== String(msg.conversation)) {
      // Only ever type into the conversation this text is for.
      return { ok: false, error: "Google Messages showed a different conversation." };
    }
    const box = await waitFor(() => first(document, COMPOSE), 15000);
    if (!box) return { ok: false, error: "Google Messages didn't show the box to type in." };
    const before = outgoingTexts().length;
    setValue(box, text);
    await pause(300);
    const button = first(document, SEND);
    // From here on Send is pressed: a missing answer is unsure, never "Nothing went".
    if (button && !button.disabled) button.click();
    else pressEnter(box);
    const want = T.squeeze(text, 4000).slice(0, 60);
    const went = await waitFor(() => {
      const now = outgoingTexts();
      return now.length > before && now.slice(before).some((t) => t.includes(want)) ? true : null;
    }, 20000);
    // Send was pressed: it may have gone, so never say it didn't.
    if (!went) return { ok: false, unsure: true, error: "Google Messages didn't show the text as sent" };
    // A text Google Messages couldn't send shows a "Not sent" note on it.
    await pause(1500);
    // Only the text just sent: an older one that failed says nothing about it.
    const failed = outgoingBubbles()
      .slice(before)
      .some((bubble) => {
        const wrap = bubble.closest("mws-message-wrapper") || bubble;
        return [...wrap.querySelectorAll("mws-message-status, [data-e2e-message-status]")].some((el) =>
          /not sent|failed/i.test(textOf(el)),
        );
      });
    if (failed) return { ok: false, error: "Google Messages says it wasn't sent." };
    return { ok: true, conversation: T.keyOf(location.href) };
  }

  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (!msg || typeof msg !== "object" || sender.id !== chrome.runtime.id || sender.tab) return false;
    if (msg.type === "texts_send_here") {
      send(msg).then(reply, (e) => reply({ ok: false, error: String(e && e.message ? e.message : e).slice(0, 200) }));
      return true;
    }
    if (msg.type === "texts_ping") {
      reply({ ok: true, paired: paired() });
      return false;
    }
    return false;
  });
})();
