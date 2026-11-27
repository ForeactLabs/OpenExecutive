"""Is a watch too noisy? Counts how many of its recent alerts were dismissed.

The watch detail page asks "only tell you about bigger changes?" when most
of a watch's recent alerts were dismissed: at least ``NOISE_MIN_DISMISSED``
of its last ``NOISE_WINDOW`` alerts. A card that expired unread (only unread
cards expire) counts as half a dismissal: nobody opened it, so it was likely
noise too, but less surely than one turned down. Raising the bar or picking "keep as is"
quiets the question by remembering the newest alert at that moment
(``config_json[NUDGE_AFTER_KEY]``); only alerts after it count again, so the
question comes back only if a fresh run of alerts is dismissed too.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from openexecutive.monitoring import store as ms
from openexecutive.monitoring.models import WatchlistItem

NOISE_WINDOW = 8
NOISE_MIN_DISMISSED = 6
NUDGE_AFTER_KEY = "_noise_nudge_after_alert_id"
_DISMISSED = "dismissed"
_EXPIRED = "expired"
_EXPIRED_WEIGHT = 0.5


class WatchNoise(BaseModel):
    """The watch's last alerts (up to ``NOISE_WINDOW``) since the question
    was last answered, how many of them were dismissed, and how many
    expired without being opened."""

    recent_alerts: int = 0
    recent_dismissed: int = 0
    recent_expired_unread: int = 0
    noisy: bool = False


def _after_id(item: WatchlistItem) -> int:
    raw = (item.config_json or {}).get(NUDGE_AFTER_KEY)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 0


def watch_noise(item: WatchlistItem, *, db_path: Path | None = None) -> WatchNoise:
    if item.id is None:
        return WatchNoise()
    rows = ms.recent_alert_statuses(
        item.id, after_alert_id=_after_id(item), limit=NOISE_WINDOW, db_path=db_path,
    )
    dismissed = sum(1 for _id, status in rows if status == _DISMISSED)
    expired = sum(1 for _id, status in rows if status == _EXPIRED)
    return WatchNoise(
        recent_alerts=len(rows),
        recent_dismissed=dismissed,
        recent_expired_unread=expired,
        noisy=dismissed + _EXPIRED_WEIGHT * expired >= NOISE_MIN_DISMISSED,
    )


def quiet_noise_nudge(item: WatchlistItem, *, db_path: Path | None = None) -> None:
    """Stop asking about the alerts the watch has raised so far."""
    if item.id is None:
        return
    rows = ms.recent_alert_statuses(item.id, limit=1, db_path=db_path)
    newest = rows[0][0] if rows else 0
    ms.set_watchlist_config_key(item.id, NUDGE_AFTER_KEY, max(newest, _after_id(item)), db_path=db_path)


__all__ = [
    "NOISE_MIN_DISMISSED",
    "NOISE_WINDOW",
    "NUDGE_AFTER_KEY",
    "WatchNoise",
    "quiet_noise_nudge",
    "watch_noise",
]
