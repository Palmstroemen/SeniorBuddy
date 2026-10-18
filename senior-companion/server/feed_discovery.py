"""
Bestmoegliche RSS-Feed-Suche fuer den Persona-Designer (Session-Notiz
2026-10-18): eine echte "RSS-Suchmaschine" gibt es nicht kostenlos/
zuverlaessig als einfache API - stattdessen wird der offene
"Feed-Autodiscovery"-Standard genutzt, mit dem Webseiten in ihrem
HTML-<head> per <link rel="alternate" type="application/rss+xml">
ihre eigenen Feeds bekanntgeben. Live getestet: diepresse.com und
faz.net liefern das direkt, derstandard.at NICHT auf der Startseite,
aber unter dem konventionellen Pfad /rss - daher zusaetzlich ein paar
uebliche Pfade als Rueckfalloption, falls Autodiscovery nichts findet.
Kein Allheilmittel, aber spart in vielen Faellen das manuelle
URL-Suchen.
"""
import logging
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

log = logging.getLogger("feed_discovery")

FEED_MIME_TYPES = {"application/rss+xml", "application/atom+xml"}
FALLBACK_PATHS = ["/rss", "/feed", "/rss.xml", "/feed.xml", "/feeds"]
MAX_RESULTS = 10


class _FeedLinkParser(HTMLParser):
    """Sammelt <link rel="alternate" type="application/rss+xml|atom+xml">
    aus dem HTML-<head> - der offene Feed-Autodiscovery-Standard."""
    def __init__(self):
        super().__init__()
        self.feeds: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        if (a.get("rel") or "").lower() != "alternate":
            return
        if (a.get("type") or "").lower() not in FEED_MIME_TYPES:
            return
        href = a.get("href")
        if href:
            self.feeds.append((href, a.get("title") or href))


def _normalize_site_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, follow_redirects=True, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    return resp.text


def _looks_like_feed(text: str) -> bool:
    """feedparser als Validitaets-Check wiederverwendet (schon
    Abhaengigkeit seit newsfeed.py) - eine normale Fehlerseite liefert
    i.d.R. keine erkennbaren entries."""
    return bool(feedparser.parse(text).entries)


async def discover_feeds(site_url: str) -> list[dict]:
    """Best effort: erst Autodiscovery-<link>-Tags auf der Startseite,
    nur wenn die NICHTS findet, zusaetzlich ein paar uebliche Pfade
    durchprobieren (spart Anfragen in den Faellen, wo Autodiscovery
    schon reicht). [] bei kompletter Erfolglosigkeit - main.py's
    Endpoint macht daraus KEINEN Fehler, der Client zeigt dann "nichts
    gefunden" an."""
    url = _normalize_site_url(site_url)
    if not url:
        return []
    async with httpx.AsyncClient() as client:
        html = await _fetch_text(client, url)
        if html is None:
            return []
        parser = _FeedLinkParser()
        parser.feed(html)
        results = [
            {"url": urljoin(url, href), "title": title}
            for href, title in parser.feeds
        ]
        if results:
            return results[:MAX_RESULTS]

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        for path in FALLBACK_PATHS:
            candidate = origin + path
            text = await _fetch_text(client, candidate)
            if text and _looks_like_feed(text):
                results.append({"url": candidate, "title": candidate})
        return results[:MAX_RESULTS]
