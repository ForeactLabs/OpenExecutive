"""Email-watch adapter: one person's own inbox (``monitoring.mailbox``).

Watchlist row shape:

  - ``signal_type``: ``"mailbox"``
  - ``target``: what it watches for in plain words ("sign-up emails",
    "Pat's reply"); the watch's name.
  - ``config_json``: ``{"mailbox_of": <People id>, "enrich": false}``.
  - ``trigger_json``: ``{"from": [addresses], "keywords": [words]}``.

Each poll reads the inbox mail that arrived since the watch was set or since
the last poll (a little overlap; the dedup key makes it once per email),
every tab, and emits one signal per email that matches, at HIGH, so the
person hears about it on their own channel as well as on a card. The signal
carries the sender's name and the subject only, never the email's text.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from openexecutive.alerts.models import AlertSeverity
from openexecutive.monitoring import mailbox
from openexecutive.monitoring.models import SOURCE_KIND_MAILBOX, Signal, WatchlistItem
from openexecutive.monitoring.sources.base import observe

logger = logging.getLogger(__name__)

# Mail read per poll. A watch is checked every few minutes, so this is far
# more than one gap's worth.
_READ_LIMIT = 50
# Re-read this far before the last poll: mail the provider listed a little
# late is still caught (the dedup key makes a re-read email a no-op).
_OVERLAP = timedelta(minutes=10)
_SUMMARY_MAX = 200
NOT_CONNECTED = "Couldn't read your inbox. Connect your mailbox under Act as me, and it will try again."
NO_OWNER = "This watch has no inbox to read."


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _since(item: WatchlistItem, now: datetime) -> datetime:
    created = _parse(item.created_at) or now
    polled = _parse(item.last_polled_at)
    return max(created, polled - _OVERLAP) if polled is not None else created


def summary_of(message: Any) -> str:
    """Who it is from and its subject, cleaned: one line, no links or
    addresses (``delegation.reminders.clean_text``)."""
    from openexecutive.delegation.reminders import clean_text

    who = clean_text(str(getattr(message, "from_name", "") or "")) or "Someone"
    subject = clean_text(str(getattr(message, "subject", "") or ""))
    line = f"{who}: {subject}" if subject else f"{who} (no subject)"
    return line[:_SUMMARY_MAX]


class MailboxSource:
    kind: str = SOURCE_KIND_MAILBOX
    # The old one-off email watch checked every 5 minutes; so does this.
    default_poll_interval_minutes: int = 5
    # Only mail from after the watch was set is read, so nothing older can
    # replay: no baseline needed.
    seed_on_first_poll: bool = False

    async def poll(self, item: WatchlistItem, *, db_path: Path | None = None) -> list[Signal]:
        from openexecutive.delegation.gmail import (
            gmail_for,
            gmail_status,
            mailbox_link,
            normalize_email,
        )
        from openexecutive.people.store import get_person

        owner = mailbox.owner_id(item)
        person = get_person(owner) if owner is not None else None
        if person is None or person.archived or item.id is None:
            observe(item, NO_OWNER)
            return []
        email = normalize_email(person.email)
        if await gmail_status(email) != "connected":
            observe(item, NOT_CONNECTED)
            return []
        now = datetime.now(UTC)
        since = _since(item, now)
        created = _parse(item.created_at) or since
        received = await gmail_for(email).received_since(since, limit=_READ_LIMIT)
        signals: list[Signal] = []
        for message in received:
            arrived = _parse(getattr(message, "received_at", "") or "")
            if arrived is not None and arrived < created:
                continue
            if not mailbox.matches(
                item.trigger_json,
                from_addr=normalize_email(getattr(message, "from_addr", "") or ""),
                from_name=str(getattr(message, "from_name", "") or ""),
                subject=str(getattr(message, "subject", "") or ""),
            ):
                continue
            link = mailbox_link(email, thread_id=message.thread_id or None, message_id=message.id)
            signals.append(Signal(
                watchlist_id=item.id,
                source_kind=SOURCE_KIND_MAILBOX,
                source_external_id=message.id,
                captured_at=now.isoformat(),
                published_at=arrived.isoformat() if arrived is not None else None,
                normalized_summary=summary_of(message),
                raw_payload={},
                provenance_url=link if link.startswith("https://") else "https://mail.google.com/",
                severity_hint=AlertSeverity.HIGH,
                dedup_key=f"mailbox:{item.id}:{message.id}",
            ))
        if not signals:
            n = len(received)
            observe(item, "Nothing new matched." if n else "No new email.")
        return signals

    def matches_trigger(self, signal: Signal, item: WatchlistItem) -> bool:
        # Matched in poll, where the sender and subject are at hand.
        return True
