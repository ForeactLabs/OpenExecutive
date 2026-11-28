"""GET /favicon/{site}: a website's own icon, for the "What I did" timeline.

A step on a website (``orchestrator/step_icons.py``) carries the site's
name, and the chat shows that site's favicon, the icon its browser tab
shows, in place of a generic globe. The server fetches it rather than the
chat page, so the viewer's browser never contacts the site and the phone
gets the same picture.

The fetch goes through ``monitoring.sources._http.fetch_bounded``: public
addresses only, every redirect checked, bodies capped. Only raster images
are served back (PNG, ICO, GIF, JPEG, WebP, sniffed from their bytes, never
SVG, which can carry script), with a ``default-src 'none'`` policy. An icon
address a page names must be https on a public domain name, never an IP
address or a port, and so must every redirect. Answers are kept in memory
per person for a day (a miss for an hour); one lookup per site runs at a
time, a few at once in all, each within a time limit, and past a short
queue a new site gets no icon for now.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response

from openexecutive.api.caller import identity_key
from openexecutive.monitoring.sources._http import fetch_bounded, fetch_bounded_at
from openexecutive.orchestrator.step_icons import site_of, valid_site

logger = logging.getLogger(__name__)

router = APIRouter()

_ICON_MAX_BYTES = 100_000
_PAGE_MAX_BYTES = 300_000
# Only the head names icons: the rest of a page is never parsed.
_HEAD_MAX_CHARS = 64_000
_HIT_TTL_S = 24 * 60 * 60
_MISS_TTL_S = 60 * 60
_CACHE_MAX = 300
_CACHE_MAX_BYTES = 8_000_000
# Lookups at once in all, and how long one may take.
_AT_ONCE = 4
_LOOKUP_S = 20.0
# Sites being looked up or waiting for a turn, at most.
_MAX_LOOKING = 8

_cache: dict[str, tuple[bytes | None, str, float]] = {}
_looking: dict[str, asyncio.Future[tuple[bytes, str] | None]] = {}
_slots = asyncio.Semaphore(_AT_ONCE)

# Raster image types by their first bytes. SVG is left out on purpose.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def image_type(body: bytes) -> str:
    """The image type ``body`` is, or ``""`` when it isn't a raster image."""
    for magic, kind in _MAGIC:
        if body.startswith(magic):
            return kind
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return ""


class _IconLinks(HTMLParser):
    """The ``<link rel="…icon…" href>`` tags of a page's head, best first."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[int, str]] = []
        # The page's first <base href>, which its relative links resolve on.
        self.base = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "base" and not self.base:
            self.base = next((v or "" for k, v in attrs if k.lower() == "href"), "").strip()
            return
        if tag != "link":
            return
        values = {k.lower(): (v or "") for k, v in attrs}
        rel = values.get("rel", "").lower().split()
        href = values.get("href", "").strip()
        if not href or "icon" not in " ".join(rel) or "mask-icon" in rel:
            return
        if values.get("type", "").lower() == "image/svg+xml" or href.lower().split("?")[0].endswith(".svg"):
            return
        size = 0
        match = re.match(r"(\d+)x\d+", values.get("sizes", ""))
        if match:
            size = int(match.group(1))
        # A small tab icon reads best at the timeline's size: 32 or 48
        # first, then larger ones, then any.
        rank = 0 if size in (32, 48, 64) else 1 if size > 64 else 2
        self.found.append((rank, href))


def allowed(url: str) -> bool:
    """Whether ``url`` is an https address on a public domain name with no
    port or sign-in: every address fetched, redirects included."""
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.port is not None or parts.username or parts.password:
            return False
    except ValueError:
        return False
    return bool(site_of(url))


def _icon_address(base: str, href: str) -> str:
    """``href`` resolved against ``base`` when ``allowed``, else ``""``."""
    try:
        url = urljoin(base, href)
    except ValueError:
        return ""
    return url if allowed(url) else ""


def icon_links(page: str, base: str) -> list[str]:
    """The icon addresses a page's head names, best first, resolved against
    ``base``: https on a public domain name only."""
    head = page[:_HEAD_MAX_CHARS]
    end = head.lower().find("</head")
    parser = _IconLinks()
    try:
        parser.feed(head if end < 0 else head[:end])
    except Exception:
        return []
    if parser.base:
        with contextlib.suppress(ValueError):
            base = urljoin(base, parser.base)
    found = (_icon_address(base, href) for _rank, href in sorted(parser.found, key=lambda x: x[0]))
    return [url for url in found if url][:3]


async def _image(url: str) -> tuple[bytes, str] | None:
    try:
        body = await fetch_bounded(url, _ICON_MAX_BYTES, hop_ok=allowed)
    except Exception:
        return None
    kind = image_type(body)
    return (body, kind) if kind else None


async def find_icon(site: str) -> tuple[bytes, str] | None:
    """``site``'s favicon: ``/favicon.ico``, else what its home page names."""
    for host in (site, f"www.{site}"):
        found = await _image(f"https://{host}/favicon.ico")
        if found:
            return found
        try:
            page, at = await fetch_bounded_at(f"https://{host}/", _PAGE_MAX_BYTES, hop_ok=allowed)
        except Exception:
            continue
        # Links resolve against the page that was served (after any
        # redirect), or its <base href>.
        for link in icon_links(page.decode("utf-8", "replace"), at):
            found = await _image(link)
            if found:
                return found
    return None


async def _look_up(site: str) -> tuple[bytes, str] | None:
    """``find_icon`` within the time limit and the lookups-at-once cap."""
    async with _slots:
        try:
            return await asyncio.wait_for(find_icon(site), _LOOKUP_S)
        except Exception:
            return None


class _Busy(Exception):
    """Too many lookups are waiting: answer no icon now, and keep nothing."""


async def _icon_once(site: str) -> tuple[bytes, str] | None:
    """One lookup per site at a time: a second ask waits on the first. Past
    ``_MAX_LOOKING`` sites waiting, a new one is turned away at once."""
    running = _looking.get(site)
    if running is not None:
        return await asyncio.shield(running)
    if len(_looking) >= _MAX_LOOKING:
        raise _Busy
    task: asyncio.Future[tuple[bytes, str] | None] = asyncio.ensure_future(_look_up(site))
    _looking[site] = task
    task.add_done_callback(lambda _t: _looking.pop(site, None))
    return await asyncio.shield(task)


def _keep(key: str, entry: tuple[bytes | None, str, float]) -> None:
    """Cache ``entry``, dropping the oldest past the count or size cap."""
    _cache.pop(key, None)
    _cache[key] = entry
    while len(_cache) > _CACHE_MAX or sum(len(b or b"") for b, _k, _t in _cache.values()) > _CACHE_MAX_BYTES:
        _cache.pop(next(iter(_cache)))


@router.get("/favicon/{site}")
async def favicon(site: str, request: Request) -> Response:
    site = site.lower()
    if not valid_site(site):
        raise HTTPException(status_code=404, detail="No icon")
    # Kept per person: how fast an answer comes never says which sites
    # someone else's steps were on.
    key = f"{identity_key(request)}|{site}"
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is None or now - cached[2] > (_HIT_TTL_S if cached[0] else _MISS_TTL_S):
        try:
            found = await _icon_once(site)
        except _Busy:
            raise HTTPException(status_code=404, detail="No icon") from None
        cached = (found[0], found[1], now) if found else (None, "", now)
        _keep(key, cached)
        if not found:
            logger.info("favicon.none")
    body, kind, _ = cached
    if not body:
        raise HTTPException(status_code=404, detail="No icon")
    return Response(
        content=body,
        media_type=kind,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
