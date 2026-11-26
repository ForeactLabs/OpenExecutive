"use client";

import { RECONNECTING_TEXT } from "@/lib/backendHealth";

// The strip a page shows while the backend can't be reached
// (useBackendHealth): it is asking again on its own, so there is nothing to
// press.
export default function ReconnectingNotice() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2.5 px-4 sm:px-6 py-2 border-b border-line bg-surface-elevated text-fg-muted flex-shrink-0"
    >
      <span className="h-3 w-3 rounded-full border-2 border-current border-t-transparent animate-spin" aria-hidden="true" />
      <p className="text-xs leading-snug">{RECONNECTING_TEXT}</p>
    </div>
  );
}
