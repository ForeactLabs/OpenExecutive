"""GET /favicon/{site}: a website's own icon for the timeline (api/routes/favicon.py)."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import favicon as favicon_route

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    favicon_route._cache.clear()
    favicon_route._looking.clear()
    # Each test runs its own event loop: a semaphore binds to the first one
    # it waits in.
    favicon_route._slots = asyncio.Semaphore(favicon_route._AT_ONCE)
    yield
    favicon_route._cache.clear()
    favicon_route._looking.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(favicon_route.router)
    return TestClient(app)


def _serve(monkeypatch: pytest.MonkeyPatch, pages: dict[str, bytes]) -> list[str]:
    asked: list[str] = []

    async def fetch(url: str, max_bytes: int, **_kw: object) -> bytes:
        asked.append(url)
        if url not in pages:
            raise RuntimeError("404")
        return pages[url]

    async def fetch_at(url: str, max_bytes: int, **kw: object) -> tuple[bytes, str]:
        return await fetch(url, max_bytes, **kw), url

    monkeypatch.setattr(favicon_route, "fetch_bounded", fetch)
    monkeypatch.setattr(favicon_route, "fetch_bounded_at", fetch_at)
    return asked


def test_serves_the_sites_favicon_and_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _serve(monkeypatch, {"https://papajohns.com/favicon.ico": PNG})
    resp = _client().get("/favicon/papajohns.com")
    assert resp.status_code == 200 and resp.content == PNG
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["content-security-policy"] == "default-src 'none'"
    _client().get("/favicon/papajohns.com")
    assert asked == ["https://papajohns.com/favicon.ico"]


def test_falls_back_to_the_icon_the_home_page_names(monkeypatch: pytest.MonkeyPatch) -> None:
    page = (
        b'<html><head><link rel="icon" type="image/svg+xml" href="/i.svg">'
        b'<link rel="icon" sizes="32x32" href="/assets/fav-32.png"></head></html>'
    )
    _serve(monkeypatch, {"https://example.com/": page, "https://example.com/assets/fav-32.png": PNG})
    resp = _client().get("/favicon/example.com")
    assert resp.status_code == 200 and resp.content == PNG


def test_never_serves_svg_or_anything_not_an_image(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {"https://evil.com/favicon.ico": SVG, "https://www.evil.com/favicon.ico": b"<html>"})
    assert _client().get("/favicon/evil.com").status_code == 404


def test_refuses_what_is_not_a_public_site_name(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _serve(monkeypatch, {})
    for bad in ("localhost", "127.0.0.1", "169.254.169.254", "a..b", "-x.com", "x_y.com", "0x7f.0x1", "10.0.0.0x1"):
        assert _client().get(f"/favicon/{bad}").status_code == 404
    assert asked == []


def test_a_page_may_name_only_https_icons_on_a_public_name() -> None:
    page = (
        '<head><link rel="icon" href="http://example.com/a.png">'
        '<link rel="icon" href="https://169.254.169.254/b.png">'
        '<link rel="icon" href="https://example.com:8443/c.png">'
        '<link rel="icon" href="https://user:pw@example.com/d.png">'
        '<link rel="icon" href="//cdn.example.com/e.png"></head>'
        '<body><link rel="icon" href="/after-head.png"></body>'
    )
    assert favicon_route.icon_links(page, "https://example.com/") == ["https://cdn.example.com/e.png"]


def test_one_lookup_per_site_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    calls: list[str] = []

    async def find(site: str) -> tuple[bytes, str] | None:
        calls.append(site)
        await asyncio.sleep(0.01)
        return PNG, "image/png"

    monkeypatch.setattr(favicon_route, "find_icon", find)

    async def both() -> list[tuple[bytes, str] | None]:
        return list(await asyncio.gather(
            favicon_route._icon_once("a", "example.com"), favicon_route._icon_once("a", "example.com"),
            favicon_route._icon_once("b", "example.com"),
        ))

    assert asyncio.run(both()) == [(PNG, "image/png")] * 3
    # One lookup each: b never rides on a's (its timing would say a asked).
    assert calls == ["example.com", "example.com"]


def test_one_person_cannot_take_every_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def slow(site: str) -> tuple[bytes, str] | None:
        await asyncio.sleep(0.05)
        return None

    monkeypatch.setattr(favicon_route, "find_icon", slow)

    async def go() -> list[object]:
        mine = [asyncio.ensure_future(favicon_route._icon_once("a", f"s{i}.com")) for i in range(favicon_route._MAX_LOOKING_EACH)]
        await asyncio.sleep(0)
        out: list[object] = []
        for who in ("a", "b"):
            try:
                await favicon_route._icon_once(who, "next.com")
                out.append(who)
            except favicon_route._Busy:
                out.append("busy")
        await asyncio.gather(*mine)
        return out

    assert asyncio.run(go()) == ["busy", "b"]


def test_a_slow_site_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def slow(site: str) -> tuple[bytes, str] | None:
        await asyncio.sleep(5)
        return PNG, "image/png"

    monkeypatch.setattr(favicon_route, "find_icon", slow)
    monkeypatch.setattr(favicon_route, "_LOOKUP_S", 0.01)
    assert asyncio.run(favicon_route._look_up("example.com")) is None


def test_the_cache_keeps_to_its_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(favicon_route, "_CACHE_MAX_BYTES", 25)
    favicon_route._keep("a.com", (b"x" * 20, "image/png", 0.0))
    favicon_route._keep("b.com", (b"y" * 20, "image/png", 0.0))
    assert list(favicon_route._cache) == ["b.com"]


def test_every_hop_must_be_https_on_a_public_name(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    async def fetch(url: str, max_bytes: int, **kw: object) -> bytes:
        seen.append(kw.get("hop_ok"))
        raise RuntimeError("404")

    async def fetch_at(url: str, max_bytes: int, **kw: object) -> tuple[bytes, str]:
        return await fetch(url, max_bytes, **kw), url

    monkeypatch.setattr(favicon_route, "fetch_bounded", fetch)
    monkeypatch.setattr(favicon_route, "fetch_bounded_at", fetch_at)
    _client().get("/favicon/example.com")
    assert seen and all(hop is favicon_route.allowed for hop in seen)
    assert favicon_route.allowed("https://cdn.example.com/i.png")
    for bad in ("http://example.com/i.png", "https://203.0.113.9/i.png", "https://example.com:8081/i.png"):
        assert not favicon_route.allowed(bad)


def test_too_many_sites_waiting_gets_no_icon_and_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _serve(monkeypatch, {"https://example.com/favicon.ico": PNG})
    monkeypatch.setattr(favicon_route, "_MAX_LOOKING", 0)
    assert _client().get("/favicon/example.com").status_code == 404
    assert asked == [] and favicon_route._cache == {}


def test_each_person_has_their_own_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _serve(monkeypatch, {"https://example.com/favicon.ico": PNG})
    keys = iter(["email:a@x.com", "email:b@x.com"])
    monkeypatch.setattr(favicon_route, "identity_key", lambda _r: next(keys))
    _client().get("/favicon/example.com")
    _client().get("/favicon/example.com")
    assert len(asked) == 2


def test_links_resolve_on_the_page_served_and_its_base() -> None:
    page = '<head><link rel="icon" href="img/fav-32.png"></head>'
    assert favicon_route.icon_links(page, "https://www.shop.com/en-us/") == ["https://www.shop.com/en-us/img/fav-32.png"]
    page = '<head><base href="https://cdn.shop.com/s/"><link rel="icon" href="fav.png"></head>'
    assert favicon_route.icon_links(page, "https://shop.com/") == ["https://cdn.shop.com/s/fav.png"]


def test_an_unchanged_icon_comes_back_as_not_modified(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {"https://papajohns.com/favicon.ico": PNG})
    first = _client().get("/favicon/papajohns.com")
    again = _client().get("/favicon/papajohns.com", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""


def test_a_dozen_new_sites_at_once_all_get_looked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def find(site: str) -> tuple[bytes, str] | None:
        await asyncio.sleep(0.01)
        return PNG, "image/png"

    monkeypatch.setattr(favicon_route, "find_icon", find)

    async def go() -> list[tuple[bytes, str] | None]:
        return list(await asyncio.gather(*(favicon_route._icon_once("a", f"s{i}.com") for i in range(10))))

    assert asyncio.run(go()) == [(PNG, "image/png")] * 10
