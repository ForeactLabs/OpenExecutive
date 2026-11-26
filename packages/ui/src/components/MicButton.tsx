"use client";

// The chat mic. Tap to talk, tap again (or reach the time limit) to stop;
// what was heard goes into the message box to read, fix and send. A short
// rising tone says it's listening and a falling one that it stopped. Where
// the browser has its own speech recognition (Chrome, Edge, Safari), the
// words show up while the person talks (`onLive`); when they stop, the
// recording is written out as before and that version replaces the live
// words. Without it (Firefox) the words come after stopping. Where speech
// to text isn't set up here (no key that hears audio), the browser's own
// recognition alone writes out the words (`alone`), so the mic still works
// in Chrome, Edge and Safari. Hidden only when neither can hear.
import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { getVoiceStatus, transcribeRecording } from "@/lib/api";
import { clock, joinDictation, pickRecordingType, recordingFilename, splitHeard } from "@/lib/dictation";
import { playMicTone } from "@/lib/micTone";

type Phase = "idle" | "listening" | "hearing";

// The longest the browser alone listens when the server didn't say.
const ALONE_MAX_SECONDS = 300;
const MIC_BLOCKED = "Open Executive can't use the microphone. Allow it in your browser's site settings.";

interface Props {
  disabled?: boolean;
  /** While listening: the words so far (`settled`) and the browser's guess
   *  at the latest ones (`pending`). Called with two empty strings as soon
   *  as listening starts, and every call is followed by one `onText`. */
  onLive?: (settled: string, pending: string) => void;
  /** The words heard, once it stops. Empty when nothing was heard. */
  onText: (text: string) => void;
  onError: (message: string | null) => void;
}

// The browser's speech recognition, where it has one. Not in every
// TypeScript DOM library, so only what the mic uses.
interface Recognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  abort(): void;
}
type RecognitionCtor = new () => Recognition;

function browserRecognition(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export default function MicButton({ disabled, onLive, onText, onError }: Props) {
  const [maxSeconds, setMaxSeconds] = useState<number | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  // No speech to text here: the browser's recognition alone hears it.
  const [alone, setAlone] = useState(false);
  const aloneRef = useRef(false);
  const [elapsed, setElapsed] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recognitionRef = useRef<Recognition | null>(null);
  // True from the start of a recording until it stops: a recognizer that
  // ends on its own starts again only while this holds.
  const wantLiveRef = useRef(false);
  const restartRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // What the browser heard, kept for when the written-out version fails.
  const heardRef = useRef("");
  // For leaving mid-recording: the box keeps what was heard and unlocks.
  const onTextRef = useRef(onText);
  useEffect(() => {
    onTextRef.current = onText;
  }, [onText]);

  useEffect(() => {
    let live = true;
    const canRecord =
      typeof window !== "undefined" &&
      typeof window.MediaRecorder !== "undefined" &&
      Boolean(navigator.mediaDevices?.getUserMedia);
    const canHear = typeof window !== "undefined" && browserRecognition() !== null;
    if (!canRecord && !canHear) return;
    // No answer at all (a slow start, a dropped line) isn't "no server": it
    // asks again a few times, with the mic hidden meanwhile.
    let tries = 0;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const load = () =>
      void getVoiceStatus().then((status) => {
        if (!live) return;
        if (status === null) {
          tries += 1;
          if (tries < 4) retry = setTimeout(load, 5000 * tries);
          return;
        }
        if (status.available && canRecord) {
          setAlone(false);
          setMaxSeconds(status.max_seconds);
        } else if (canHear) {
          setAlone(true);
          setMaxSeconds(status.max_seconds || ALONE_MAX_SECONDS);
        }
      });
    load();
    return () => {
      live = false;
      if (retry) clearTimeout(retry);
    };
  }, []);

  const clearTimer = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  };

  const stopRecognition = () => {
    wantLiveRef.current = false;
    if (restartRef.current) clearTimeout(restartRef.current);
    restartRef.current = null;
    const rec = recognitionRef.current;
    recognitionRef.current = null;
    if (!rec) return;
    rec.onresult = null;
    rec.onerror = null;
    rec.onend = null;
    try {
      rec.abort();
    } catch {
      // Already ended.
    }
  };

  // The browser alone stopped (a tap, the time limit, or it failed): what
  // it heard goes into the box.
  const finishAlone = useCallback(
    (error: string | null) => {
      if (!aloneRef.current) return;
      aloneRef.current = false;
      clearTimer();
      stopRecognition();
      playMicTone("stop");
      const heard = heardRef.current;
      onText(heard);
      if (error === "not-allowed" || error === "service-not-allowed" || error === "audio-capture") onError(MIC_BLOCKED);
      else if (error === "network" && !heard.trim()) {
        // Some browsers (Brave, Opera, Electron shells) have the recognizer
        // but nothing behind it: the mic goes for the rest of the page.
        onError("This browser can't turn speech into words on its own. Type instead.");
        setMaxSeconds(null);
      } else if (!heard.trim()) onError(error ? "Couldn't hear it. Try again." : "Didn't catch any words. Try again.");
      setPhase("idle");
    },
    [onError, onText],
  );

  // Leaving the page mid-recording lets the microphone go.
  useEffect(
    () => () => {
      clearTimer();
      stopRecognition();
      if (aloneRef.current) {
        aloneRef.current = false;
        onTextRef.current(heardRef.current);
      }
      const rec = recorderRef.current;
      if (rec && rec.state !== "inactive") {
        rec.onstop = null;
        rec.stop();
        rec.stream.getTracks().forEach((t) => t.stop());
        onTextRef.current(heardRef.current);
      }
    },
    [],
  );

  const stop = useCallback(() => {
    if (aloneRef.current) {
      finishAlone(null);
      return;
    }
    clearTimer();
    stopRecognition();
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") rec.stop();
  }, [finishAlone]);

  // Live words from the browser's own recognition. It ends on its own after
  // a pause or a minute, so it starts again (keeping what it had, a little
  // later each time) until the recording stops. An error other than silence
  // ends live words for this recording; the words still come when it stops.
  // Alone, they are the only words, so it starts again on Android too, and
  // an error ends the listening there and then.
  const listen = useCallback(() => {
    const Ctor = browserRecognition();
    const by = aloneRef.current;
    if (!Ctor || (!onLive && !by)) return;
    let earlier = "";
    let restarts = 0;
    // Chrome on Android plays its own sound each time recognition starts,
    // so there it runs once: after a long pause the rest comes on stop.
    // It also repeats earlier words in each piece it sends.
    const android = /Android/i.test(navigator.userAgent);
    const maxRestarts = by ? 200 : android ? 0 : 20;
    let failed: string | null = null;
    wantLiveRef.current = true;
    const begin = () => {
      if (!wantLiveRef.current) return;
      let rec: Recognition;
      try {
        rec = new Ctor();
        rec.continuous = true;
        rec.interimResults = true;
        rec.lang = navigator.language || "en-US";
      } catch {
        return;
      }
      // Words still pending when it ends on its own are kept too.
      let latest = earlier;
      rec.onresult = (e) => {
        const parts = Array.from(e.results, (r) => ({ transcript: r[0]?.transcript ?? "", isFinal: r.isFinal }));
        const { settled, pending } = splitHeard(earlier, parts, android);
        latest = joinDictation(settled, pending);
        heardRef.current = latest;
        onLive?.(settled, pending);
      };
      rec.onerror = (e) => {
        if (e.error !== "no-speech" && e.error !== "aborted") failed = e.error;
      };
      rec.onend = () => {
        earlier = latest;
        if (recognitionRef.current !== rec) return;
        recognitionRef.current = null;
        if (failed || restarts >= maxRestarts) {
          if (by) finishAlone(failed);
          return;
        }
        restarts += 1;
        restartRef.current = setTimeout(begin, by ? 100 : Math.min(2000, 200 * restarts));
      };
      recognitionRef.current = rec;
      try {
        rec.start();
      } catch {
        recognitionRef.current = null;
        if (by) finishAlone("start");
      }
    };
    begin();
  }, [onLive, finishAlone]);

  const start = useCallback(async () => {
    if (maxSeconds === null) return;
    onError(null);
    if (alone) {
      // The browser's recognition asks for the microphone itself.
      playMicTone("start");
      aloneRef.current = true;
      heardRef.current = "";
      onLive?.("", "");
      const began = Date.now();
      setElapsed(0);
      setPhase("listening");
      timerRef.current = setInterval(() => {
        const secs = (Date.now() - began) / 1000;
        setElapsed(secs);
        if (secs >= maxSeconds) stop();
      }, 250);
      listen();
      return;
    }
    // The tone plays on the tap itself, before any permission question.
    playMicTone("start");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      onError(MIC_BLOCKED);
      return;
    }
    const type = pickRecordingType((t) => MediaRecorder.isTypeSupported(t));
    const rec = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    rec.onstop = async () => {
      playMicTone("stop");
      stream.getTracks().forEach((t) => t.stop());
      recorderRef.current = null;
      stopRecognition();
      const heard = heardRef.current;
      const mime = rec.mimeType || type || "audio/webm";
      const recording = new Blob(chunks, { type: mime });
      if (recording.size === 0) {
        onText(heard);
        setPhase("idle");
        return;
      }
      setPhase("hearing");
      try {
        const text = await transcribeRecording(recording, recordingFilename(mime));
        if (text.trim() || heard) onText(text.trim() ? text : heard);
        else {
          onText("");
          onError("Didn't catch any words. Try again.");
        }
      } catch (err) {
        // The words already on screen are kept when writing it out fails.
        onText(heard);
        if (!heard) onError(err instanceof Error ? err.message : "Couldn't hear it. Try again.");
      } finally {
        setPhase("idle");
      }
    };
    recorderRef.current = rec;
    heardRef.current = "";
    rec.start();
    onLive?.("", "");
    listen();
    const began = Date.now();
    setElapsed(0);
    setPhase("listening");
    timerRef.current = setInterval(() => {
      const secs = (Date.now() - began) / 1000;
      setElapsed(secs);
      if (secs >= maxSeconds) stop();
    }, 250);
  }, [alone, maxSeconds, onError, onLive, onText, stop, listen]);

  if (maxSeconds === null) return null;

  if (phase === "listening") {
    return (
      <button
        type="button"
        onClick={stop}
        aria-label="Stop and use what you said"
        title="Stop — your words go into the message box"
        className="flex-shrink-0 h-11 px-3 rounded-xl border border-red-500/50 text-fg flex items-center gap-2 cursor-pointer"
      >
        <span className="w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse" aria-hidden />
        <span className="text-sm tabular-nums">{clock(elapsed)}</span>
        <Icon name="stop" size="w-3.5 h-3.5" fill="currentColor" className="text-red-500" />
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={() => void start()}
      disabled={disabled || phase === "hearing"}
      aria-label={phase === "hearing" ? "Writing out what you said" : "Talk instead of typing"}
      title={phase === "hearing" ? "Writing it out…" : `Talk — up to ${clock(maxSeconds)}`}
      className="flex-shrink-0 w-11 h-11 rounded-xl border border-line-strong text-fg-muted hover:text-fg hover:border-fg-muted disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center cursor-pointer"
    >
      {phase === "hearing" ? (
        <span className="w-4 h-4 border-2 border-fg-muted border-t-transparent rounded-full animate-spin" aria-hidden />
      ) : (
        <Icon name="microphone" size="w-5 h-5" />
      )}
    </button>
  );
}
