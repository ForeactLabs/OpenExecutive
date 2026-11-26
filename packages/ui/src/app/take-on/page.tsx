"use client";

// See all of Home's "I could also" (components/briefing/TakeOn.tsx).

import Link from "next/link";
import { useRouter } from "next/navigation";

import TakeOn from "@/components/briefing/TakeOn";

export default function TakeOnPage() {
  const router = useRouter();
  return (
    <main className="flex-1 min-h-0 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
        <header className="mb-6">
          <Link href="/" className="text-sm font-medium text-accent hover:underline">
            ← Home
          </Link>
          <h1 className="mt-2 text-2xl sm:text-3xl font-bold tracking-tight text-fg">I could also</h1>
          <p className="mt-1 text-[15px] text-fg-muted">
            Real steps I can take for you now. Nothing runs until you press Do it.
          </p>
        </header>
        <TakeOn all onOpenChat={(id) => router.push(`/?session=${encodeURIComponent(id)}`)} />
      </div>
    </main>
  );
}
