// Open Executive browser extension: the script on Google Messages.
//
// Google Messages for web (messages.google.com) shows the texts on the
// person's Android phone once they pair it, and sends from that phone. When
// they turn on Texts in Open Executive, the background keeps this page open,
// in the extension's own window, in the "Open Executive" tab group. This script:
//
// - tells the background what the page shows: whether it is paired, and each
//   conversation whose latest text changed since it last said (the
//   background passes it on to Open Executive, signed in as the person);
// - types and sends a text the background asks for, in a conversation or to
//   a new number, and says whether it went.
//
// It reads only the conversation list (each conversation's latest text) and
// the conversation it was asked to send in, and it sends only what the background hands it (a text the
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

  /** Whether two phone numbers are the same, by their last ten digits. */
  T.sameNumber = function (a, b) {
    const x = String(a || "").replace(/\D/g, "").slice(-10);
    const y = String(b || "").replace(/\D/g, "").slice(-10);
    return x.length >= 7 && x === y;
  };

  // Sign-in codes are never sent to Open Executive (the same patterns as
  // openexecutive/delegation/sign_in_codes.py, which drops any that come).
  const CODE_RIGHT_AFTER = /\b(?:codes?|passcodes?|passwords?|passwd|otp|pins?|tokens?)\b[\s:#-]*(?:(?:is|was)\b[\s:#-]*)?(\d{4,10}|\d{3}[- ]\d{3}|(?:\d ){3,7}\d)(?!\d)/i;
  const SIGN_IN_WORDS = /\b(one[- ]time|verification|verify|sign(?:ing)?[- ]?in|log(?:ging)?[- ]?in|2fa|mfa|authenticat\w*)\b/i;
  const CODE_DIGITS = /(?<!\d)(\d{5,10}|\d{3}[- ]\d{3}|(?:\d ){3,7}\d)(?!\d)/;
  const SIGN_IN_LINK = /\b(magic link|sign[- ]?in link|log[- ]?in link|is this you signing in|approve (?:this |the )?sign[- ]?in|tap to (?:sign|log) in|trying to sign in)\b/i;
  const ENTER_CODE = /\b(?:enter|type)\s+((?!(?:19|20)\d\d(?!\d))\d{4,10}(?!\d)|(?:\d ){3,7}\d)/i;

  /** Whether the words read like a sign-in code or a sign-in link. */
  T.isSignInCode = function (text) {
    const s = String(text || "");
    return SIGN_IN_LINK.test(s) || ENTER_CODE.test(s) || CODE_RIGHT_AFTER.test(s) || (SIGN_IN_WORDS.test(s) && CODE_DIGITS.test(s));
  };

  /** One line of text, at most `max` characters. */
  T.squeeze = function (text, max) {
    return String(text || "").replace(/\s+/g, " ").trim().slice(0, max);
  };

  // The person's own last text starts with this in the list.
  // A hint only (the first look skips these): the server decides which texts
// were sent from here, by its own sends, whatever the language.
  const YOU = /^(You|Vous|Tú|Tu|Du|Jij|Je|U|Você|Ty|Ви|Вы|Ти|Sen|Siz|Anda|Kamu|Bạn|Eu|Io|Jeg|Jag|Minä|Sinä|자신|나|あなた|自分|我|你)\s*[:：]\s*/i;

  /**
   * A conversation in the list as plain data, or null:
   * { key, title, snippet, unread, outgoing, group, time }.
   */
  T.fromRow = function (row) {
    if (!row || typeof row.key !== "string" || !row.key || row.key === "new") return null;
    const title = T.squeeze(row.title, 200);
    // Kept as shown: anyone can start a text with "You:", so the words
    // only hint that it is theirs (the server knows its own sends).
    const snippet = T.squeeze(row.snippet, 4000);
    const outgoing = YOU.test(snippet);
    return {
      key: row.key.slice(0, 300),
      title,
      snippet,
      unread: Boolean(row.unread),
      outgoing,
      // Only the page's own mark: "Smith, John" is one person.
      group: Boolean(row.group),
      time: T.squeeze(row.time, 80),
    };
  };

  const DAY = 24 * 60 * 60 * 1000;
  const WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

  /**
   * When the latest text in a row came, in ms, from the time the list shows
   * ("Now", "5 min", "2:15 PM", "Yesterday", "Mon", "Nov 10"), or null when
   * it can't be read (another language, a new layout).
   */
  T.timeOf = function (shown, now) {
    const s = T.squeeze(shown, 80).toLowerCase().replace(/\u202f/g, " ");
    if (!s) return null;
    if (/^(just )?now$/.test(s)) return now;
    let m = /^(\d+)\s*(m|min|mins|minutes?)$/.exec(s);
    if (m) return now - Number(m[1]) * 60000;
    m = /^(\d+)\s*(h|hr|hrs|hours?)$/.exec(s);
    if (m) return now - Number(m[1]) * 3600000;
    if (s === "yesterday") return now - DAY;
    m = /^(\d{1,2}):(\d{2})\s*(am|pm)?$/.exec(s);
    if (m) {
      let h = Number(m[1]) % (m[3] ? 12 : 24);
      if (m[3] === "pm") h += 12;
      const d = new Date(now);
      d.setHours(h, Number(m[2]), 0, 0);
      const t = d.getTime();
      return t > now + 60000 ? t - DAY : t;
    }
    const day = WEEKDAYS.indexOf(s.slice(0, 3));
    if (day >= 0 && /^[a-z]+\.?$/.test(s)) {
      const back = (new Date(now).getDay() - day + 7) % 7 || 7;
      return now - back * DAY;
    }
    // A date without a year ("Nov 10") is this year's, or last year's when
    // that would be ahead of now.
    const withYear = /\d{4}/.test(s) ? s : `${s} ${new Date(now).getFullYear()}`;
    const t = Date.parse(withYear);
    if (Number.isNaN(t)) return null;
    return t > now + DAY ? Date.parse(`${s} ${new Date(now).getFullYear() - 1}`) : t;
  };

  // How far back a first look reads the list (Open Executive keeps 30 days).
  const KEEP = 30 * DAY;

  /**
   * What to tell Open Executive about the list, given what it said before
   * (`seen`: key -> fingerprint of the latest text). The first time (`seen`
   * is null) every conversation the list shows from the last 30 days is
   * told, so the person can ask about texts from before they turned texts
   * on. Only unread ones from the last day count as new; the rest go as
   * `earlier` (kept to read, never answered as if they just came). Each text
   * carries the time the list shows, or now when it can't be read (it
   * changed while the page was watching). `allEarlier`: a first look after
   * an update forgot what was seen, so nothing in it is new (each was told
   * before). A sign-in code is never told. Returns { items, seen }.
   */
  T.changes = function (rows, seen, now, allEarlier) {
    const first = seen === null || typeof seen !== "object";
    const next = first ? {} : { ...seen };
    const items = [];
    for (const raw of rows) {
      const row = T.fromRow(raw);
      if (!row || !row.snippet) continue;
      const print = T.hash(`${row.outgoing ? "1" : "0"}:${row.snippet}`);
      // What was seen is "<fingerprint>.<count of changes>": the count tells
      // the same words again later ("ok") from the same report sent again.
      const [beforePrint, beforeCount] = String((first ? "" : next[row.key]) || "").split(".");
      if (beforePrint === print) continue;
      const count = (parseInt(beforeCount, 10) || 0) + 1;
      next[row.key] = `${print}.${count}`;
      const shown = T.timeOf(row.time, now);
      // A first look can't date a row it can't read the time of: left out.
      if (first && (shown === null || now - shown > KEEP)) continue;
      if (T.isSignInCode(row.snippet)) continue;
      const earlier = first && (allEarlier === true || !row.unread || row.outgoing || now - shown > DAY);
      items.push({
        // The same change, reported again (an answer that never came back),
        // is the same ref, so it is kept once.
        ref: `${row.key}:${print}:${count}`,
        conversation: row.key,
        title: row.title,
        sender: row.group ? "" : row.title,
        text: row.snippet,
        at: new Date(shown === null ? now : Math.min(shown, now)).toISOString(),
        number: T.looksLikeNumber(row.title) ? row.title.slice(0, 40) : "",
        is_group: row.group,
        // An older Open Executive doesn't know `earlier`: it reads this hint
        // as "don't answer it", which is what earlier means too.
        outgoing: row.outgoing || earlier,
        earlier,
      });
    }
    // Forget conversations far down the list, so the map stays small.
    const keys = Object.keys(next);
    if (keys.length > 500) for (const k of keys.slice(0, keys.length - 500)) delete next[k];
    // New texts first, so a long first look never crowds one out.
    items.sort((a, b) => Number(a.earlier) - Number(b.earlier));
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
        time: (() => {
          const el = first(item, ["mws-relative-timestamp", "[data-e2e-conversation-timestamp]", ".timestamp"]);
          return el ? el.getAttribute("aria-label") || el.getAttribute("title") || textOf(el) : "";
        })(),
      });
    }
    return rows;
  }

  function paired() {
    if (/\/web\/(authentication|welcome)/.test(location.pathname)) return false;
    return Boolean(document.querySelector('mws-conversations-list, a[href*="/web/conversations/"]'));
  }

  // ---- Telling the background ----

  // Renamed when a first look began reading earlier texts, so an update
  // reads them once too: as earlier, since what the old key saw was told
  // already (answering it again would be a second turn on the same text).
  // The old key is dropped once the new one is kept.
  const SEEN_KEY = "textsSeen2";
  const OLD_SEEN_KEY = "textsSeen";
  async function keepSeen(seen) {
    await chrome.storage.local.set({ [SEEN_KEY]: seen });
    await chrome.storage.local.remove(OLD_SEEN_KEY);
  }
  let reporting = false;
  let lastReport = 0;
  // What the last report said about pairing: a change goes at once, so the
  // site's setup ticks soon after the code is scanned.
  let lastPaired = null;
  // Fast tries at telling a pairing change: after a minute of them, back to
  // once a minute, so a site that never takes it isn't sent one every 10 s.
  let fastTries = 0;

  let again = false;

  async function report(force) {
    // A change while a report is out goes right after it, not at the next tick.
    if (reporting) {
      again = true;
      return;
    }
    reporting = true;
    again = false;
    try {
      // Off: read nothing at all, and forget what was seen, so turning it
      // back on is a first look again (only unread texts count as new).
      if ((await chrome.storage.local.get("textsOn")).textsOn !== true) {
        await chrome.storage.local.remove(SEEN_KEY);
        return;
      }
      const now = Date.now();
      // Showing Use here: read nothing, and say only that it isn't live here.
      // The background passes that on only when no other tab is live.
      const away = Boolean(elsewhere());
      const isPaired = !away && paired();
      let items = [];
      let seen = null;
      if (isPaired) {
        const kept = await chrome.storage.local.get([SEEN_KEY, OLD_SEEN_KEY]);
        const stored = kept[SEEN_KEY];
        const updated = !(stored && typeof stored === "object") && Boolean(kept[OLD_SEEN_KEY]);
        const out = T.changes(listRows(), stored && typeof stored === "object" ? stored : null, now, updated);
        items = out.items;
        seen = out.seen;
      }
      // Nothing new: still say it's here, once a minute. A pairing change the
      // site hasn't taken yet goes sooner, at most every 10 seconds.
      const wait = isPaired === lastPaired ? 60000 : 10000;
      if (!items.length && !force && now - lastReport < wait) {
        if (seen) await keepSeen(seen);
        return;
      }
      lastReport = now;
      const answer = await chrome.runtime.sendMessage({ type: "texts_seen", paired: isPaired, items, elsewhere: away }).catch(() => null);
      // A pairing change counts as told once the site took it, or once no
      // site was open to tell: a site opened later hears it within a minute.
      if (isPaired === lastPaired) fastTries = 0;
      else if ((answer && (answer.ok === true || answer.nobody === true)) || ++fastTries >= 6) {
        lastPaired = isPaired;
        fastTries = 0;
      }
      // Only what the site took counts as seen: with no Open Executive tab
      // to take them (or a failed one), the same texts go again next time.
      if (seen && ((answer && answer.ok === true) || !items.length)) await keepSeen(seen);
    } finally {
      reporting = false;
      if (again) soon();
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

  // Google Messages runs in one tab at a time. In any other tab it shows
  // "Use here" over the page, and the box to type in never comes.
  function elsewhere() {
    return [...document.querySelectorAll("button")].find((b) => /^\s*use here\s*$/i.test(textOf(b))) || null;
  }

  function setValue(el, text) {
    el.focus();
    if (el.isContentEditable) {
      document.execCommand("selectAll", false);
      if (text) document.execCommand("insertText", false, text);
      else document.execCommand("delete", false);
      return;
    }
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, text);
    else el.value = text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Google Messages draws the box to type in as an editable div
  // (`div[contenteditable][role=textbox][data-e2e-message-input-box]`),
  // not a textarea, so that comes first; the textarea forms are for older
  // versions of the page.
  const COMPOSE = [
    '[data-e2e-message-input-box][contenteditable="true"]',
    "textarea[data-e2e-message-input-box]",
    "mws-message-compose textarea",
    "mws-autosize-textarea textarea",
    "textarea.input",
    "textarea",
    'mws-message-compose [contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
  ];
  const SEND = [
    "[data-e2e-send-text-button]",
    "mws-message-send-button button",
    'button[aria-label^="Send" i]',
  ];

  // Google Messages draws two Send buttons: an inline one inside a hidden
  // (display: none) wrapper and the round one people press. The first in
  // the page is the hidden one, and clicking it sends nothing, so take the
  // first one that is actually on screen and enabled.
  // The first selector with a button on screen decides: if that button is
  // still disabled, nothing is returned (and Enter is used), so a looser
  // selector further down never picks some other "Send…" button.
  function sendButton() {
    for (const sel of SEND) {
      const shown = [...document.querySelectorAll(sel)].filter((b) => b.getClientRects().length > 0);
      if (shown.length) return shown.find((b) => !b.disabled) || null;
    }
    return null;
  }

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
    const pick = await waitFor(() => {
      const el = document.querySelector("[data-e2e-send-to-button]");
      return el && T.sameNumber(textOf(el), number) ? el : null;
    }, 6000);
    if (!pick) return "Google Messages didn't offer to send to that number. Nothing went.";
    pick.click();
    const opened = await waitFor(() => T.keyOf(location.href), 15000);
    return opened ? "" : "Google Messages didn't open a conversation with that number.";
  }

  async function send(msg) {
    const text = String(msg.text || "");
    if (!text.trim()) return { ok: false, error: "There was nothing to send." };
    // Live in another tab: never take it from there, where the person may be
    // typing. Only when no tab was live may this one take over.
    // The page may draw "Use here" a moment after it loads: wait for it, or
    // for the box to type in (or the number to type, for someone new).
    await waitFor(
      () => elsewhere() || first(document, COMPOSE) || first(document, ["input[data-e2e-contact-input]", "mw-contact-chips-input input"]),
      10000,
    );
    const takeOver = elsewhere();
    if (takeOver && msg.mayTakeOver !== true) return { ok: false, error: "Google Messages is open in another tab. Nothing went." };
    if (takeOver) {
      takeOver.click();
      await waitFor(() => !elsewhere() && paired(), 15000);
    }
    if (!paired()) return { ok: false, error: "Google Messages isn't paired with their phone." };
    if (!msg.conversation) {
      const problem = await startConversation(String(msg.number || ""));
      if (problem) return { ok: false, error: problem };
    } else if (T.keyOf(location.href) !== String(msg.conversation)) {
      // Only ever type into the conversation this text is for.
      return { ok: false, error: "Google Messages showed a different conversation." };
    }
    // The conversation it is in now: the one it was sent to, or the one a
    // new number opened. The person may be using this tab too.
    const key = T.keyOf(location.href);
    const box = await waitFor(() => first(document, COMPOSE), 15000);
    if (!box) {
      // Say what the page showed instead, so the next look knows where to start.
      const why = elsewhere()
        ? " It was open in another tab and didn't take over here."
        : !document.querySelector("mws-message-wrapper, mws-messages-list")
          ? " The conversation didn't open."
          : "";
      return { ok: false, error: `Google Messages didn't show the box to type in.${why} Nothing went.` };
    }
    if (T.keyOf(location.href) !== key) return { ok: false, error: "Google Messages moved to another conversation. Nothing went." };
    const before = outgoingTexts().length;
    setValue(box, text);
    await pause(300);
    // The page enables Send a moment after the text goes in. Wait before the
    // check below, so nothing awaits between that check and the click.
    const button = await waitFor(() => sendButton(), 2000);
    // Right before Send: still the same conversation, and still its box.
    if (!key || T.keyOf(location.href) !== key || !box.isConnected) {
      if (box.isConnected) setValue(box, "");
      return { ok: false, error: "Google Messages moved to another conversation. Nothing went." };
    }
    // Google Messages ignores a press made from the page, so Chrome makes a
    // real one on the Send button (see pressInTab in background.js).
    const spot = button && button.getBoundingClientRect();
    if (!spot) {
      if (box.isConnected) setValue(box, "");
      return { ok: false, error: "Google Messages didn't show its Send button. Nothing went." };
    }
    const pressed = await chrome.runtime
      .sendMessage({ type: "texts_press", x: spot.left + spot.width / 2, y: spot.top + spot.height / 2 })
      .catch(() => null);
    // No answer, or a press that failed part way, may still have gone: only a
    // press refused before it started means nothing went.
    if (pressed && pressed.ok !== true && pressed.maybe !== true) {
      // No real press happened, so nothing went: take the text back out.
      if (box.isConnected) setValue(box, "");
      return { ok: false, error: "Chrome didn't let Open Executive press Send in Google Messages. Nothing went." };
    }
    // From here on Send is pressed: a missing answer is unsure, never "Nothing went".
    const want = T.squeeze(text, 4000).slice(0, 60);
    // Sent means both: the box emptied and the text shows as a new bubble.
    const went = await waitFor(() => {
      const now = outgoingTexts();
      const emptied = !box.isConnected || !T.squeeze(textOf(box), 10);
      return emptied && now.length > before && now.slice(before).some((t) => t.includes(want)) ? true : null;
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
      reply({ ok: true, paired: paired(), elsewhere: Boolean(elsewhere()) });
      return false;
    }
    if (msg.type === "texts_busy") {
      // Something half-typed in the box: the person is writing here.
      const box = first(document, COMPOSE);
      reply({ busy: Boolean(box && T.squeeze(box.value !== undefined ? box.value : textOf(box), 10)) });
      return false;
    }
    return false;
  });
})();
