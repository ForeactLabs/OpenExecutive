// The chat mic: what was heard goes into the message box, where the person
// reads it and sends it like anything typed. Pure helpers, so they test
// without a browser.

/** `heard` added after what the box already holds, one space between. */
export function joinDictation(existing: string, heard: string): string {
  const words = heard.trim();
  if (!words) return existing;
  if (!existing.trim()) return words;
  return /\s$/.test(existing) ? existing + words : `${existing} ${words}`;
}

// Chrome and Firefox record webm, Safari mp4. The first the browser takes.
const RECORDING_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

export function pickRecordingType(isSupported: (type: string) => boolean): string | undefined {
  return RECORDING_TYPES.find((t) => {
    try {
      return isSupported(t);
    } catch {
      return false;
    }
  });
}

/** A filename whose suffix the speech service reads the format from. */
export function recordingFilename(mimeType: string): string {
  const type = mimeType.split(";")[0].trim().toLowerCase();
  if (type === "audio/mp4") return "recording.m4a";
  if (type === "audio/ogg") return "recording.ogg";
  return "recording.webm";
}

/** 0:07, 1:42 */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** One piece of what the browser's own speech recognition heard so far. */
export interface HeardPart {
  transcript: string;
  isFinal: boolean;
}

/** The words shown while the person talks: `settled` (what the browser
 *  won't change any more, after `earlier`) and `pending` (its current
 *  guess at the latest words, which may still change). */
export function splitHeard(
  earlier: string,
  parts: HeardPart[],
  cumulative = false,
): { settled: string; pending: string } {
  // Chrome on Android (`cumulative`) sends each piece with everything said
  // before it ("hi", "hi there", "hi there how"), where desktop sends only
  // the new words. There a piece that starts with the one before it
  // replaces that one, so the words don't repeat.
  const pieces: HeardPart[] = [];
  for (const part of parts) {
    const text = part.transcript.trim();
    if (!text) continue;
    const prev = pieces[pieces.length - 1];
    // Android may add a comma or full stop as it revises ("hello" then
    // "hello, how are you"), so trailing punctuation doesn't count.
    const before = prev?.transcript.toLowerCase().replace(/[.,;:!?]+$/, "");
    const lower = text.toLowerCase();
    const next = lower.charAt(before?.length ?? 0);
    if (cumulative && before && lower.startsWith(before) && (!next || /[\s.,;:!?]/.test(next))) pieces.pop();
    pieces.push({ transcript: text, isFinal: part.isFinal });
  }
  let settled = earlier.trim();
  let pending = "";
  for (const part of pieces) {
    if (part.isFinal) settled = joinDictation(settled, part.transcript);
    else pending = joinDictation(pending, part.transcript);
  }
  return { settled, pending };
}

/** The two short tones: rising when the mic starts, falling when it stops. */
export const MIC_TONES: Record<"start" | "stop", readonly [number, number]> = {
  start: [660, 880],
  stop: [880, 660],
};

// The longest the browser alone listens when the server didn't say.
export const ALONE_MAX_SECONDS = 300;
export const MIC_BLOCKED = "Open Executive can't use the microphone. Allow it in your browser's site settings.";

export type MicMode = { mode: "record" | "alone"; maxSeconds: number } | { mode: "hidden" } | { mode: "ask-again" };

/** How the chat mic works here, from `/chat/voice` (`null`: no answer) and
 *  what the browser can do: record for the server to write out, or let the
 *  browser's own recognition hear it alone, or nothing. No answer at all
 *  isn't "no server", so it asks again rather than pick. */
export function micMode(
  status: { available: boolean; max_seconds: number } | null,
  canRecord: boolean,
  canHear: boolean,
): MicMode {
  if (status === null) return canRecord || canHear ? { mode: "ask-again" } : { mode: "hidden" };
  if (status.available && canRecord) return { mode: "record", maxSeconds: status.max_seconds };
  if (canHear) return { mode: "alone", maxSeconds: status.max_seconds || ALONE_MAX_SECONDS };
  return { mode: "hidden" };
}

/** When to ask `/chat/voice` again after the `tries`th call got no answer,
 *  in ms, or null to stop: three more tries, 5, 10 and 15 s apart. */
export function askAgainAfter(tries: number): number | null {
  return tries < 4 ? 5000 * tries : null;
}

/** What the browser hearing alone ending says (`error`: the recognizer's
 *  error, or null for a tap or the time limit), and whether the mic goes
 *  for the rest of the page: some browsers (Brave, Opera, Electron shells)
 *  have the recognizer but nothing behind it, and every run ends "network". */
export function aloneEnd(error: string | null, heard: string): { message: string | null; hide: boolean } {
  if (error === "not-allowed" || error === "service-not-allowed" || error === "audio-capture") {
    return { message: MIC_BLOCKED, hide: false };
  }
  if (heard.trim()) return { message: null, hide: false };
  if (error === "network") {
    return { message: "This browser can't turn speech into words on its own. Type instead.", hide: true };
  }
  return { message: error ? "Couldn't hear it. Try again." : "Didn't catch any words. Try again.", hide: false };
}
