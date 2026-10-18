"""
Tests fuer die bestmoegliche RSS-Feed-Suche (server/feed_discovery.py).
Wie test_newsfeed.py: NIE ein echter HTTP-Request, ausschliesslich
gemockter Verkehr (pytest-httpx).
"""
import feed_discovery

SAMPLE_FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Testfeed</title>
<item><title>Titel A</title><description>Beschreibung A</description></item>
</channel></rss>"""

NOT_A_FEED_HTML = "<html><body><h1>404 - Seite nicht gefunden</h1></body></html>"


def test_normalize_site_url_adds_https_when_missing_scheme():
    assert feed_discovery._normalize_site_url("diepresse.com") == "https://diepresse.com"


def test_normalize_site_url_leaves_existing_scheme_untouched():
    assert feed_discovery._normalize_site_url("http://example.com") == "http://example.com"


def test_normalize_site_url_strips_whitespace():
    assert feed_discovery._normalize_site_url("  example.com  ") == "https://example.com"


async def test_discover_feeds_finds_autodiscovery_link(httpx_mock):
    html = (
        '<html><head><link rel="alternate" type="application/rss+xml" '
        'title="Politik" href="https://example.com/rss/politik.xml"></head>'
        "<body></body></html>"
    )
    httpx_mock.add_response(url="https://example.com", text=html)

    results = await feed_discovery.discover_feeds("example.com")

    assert results == [{"url": "https://example.com/rss/politik.xml", "title": "Politik"}]


async def test_discover_feeds_resolves_relative_href_against_site(httpx_mock):
    html = (
        '<html><head><link rel="alternate" type="application/rss+xml" '
        'href="/rss/politik.xml"></head><body></body></html>'
    )
    httpx_mock.add_response(url="https://example.com", text=html)

    results = await feed_discovery.discover_feeds("example.com")

    # Ohne title-Attribut faellt der Titel auf den rohen (nicht
    # aufgeloesten) href zurueck - nur die URL selbst wird aufgeloest.
    assert results == [{"url": "https://example.com/rss/politik.xml", "title": "/rss/politik.xml"}]


async def test_discover_feeds_falls_back_to_conventional_paths(httpx_mock):
    # discover_feeds() probiert ALLE FALLBACK_PATHS durch (sammelt jeden
    # Treffer, nicht nur den ersten) - jeder Pfad braucht hier also eine
    # registrierte Antwort, sonst beschwert sich httpx_mock ueber einen
    # unerwarteten Request.
    html = "<html><head></head><body>keine Autodiscovery hier</body></html>"
    httpx_mock.add_response(url="https://example.com", text=html)
    for path in feed_discovery.FALLBACK_PATHS:
        if path == "/feed":
            httpx_mock.add_response(url="https://example.com/feed", text=SAMPLE_FEED_XML)
        else:
            httpx_mock.add_response(url=f"https://example.com{path}", status_code=404)

    results = await feed_discovery.discover_feeds("example.com")

    assert results == [{"url": "https://example.com/feed", "title": "https://example.com/feed"}]


async def test_discover_feeds_does_not_count_non_feed_200_as_a_hit(httpx_mock):
    html = "<html><head></head><body>keine Autodiscovery hier</body></html>"
    httpx_mock.add_response(url="https://example.com", text=html)
    for path in feed_discovery.FALLBACK_PATHS:
        httpx_mock.add_response(url=f"https://example.com{path}", text=NOT_A_FEED_HTML)

    results = await feed_discovery.discover_feeds("example.com")

    assert results == []


async def test_discover_feeds_returns_empty_list_when_site_unreachable(httpx_mock):
    httpx_mock.add_response(url="https://example.com", status_code=500)

    results = await feed_discovery.discover_feeds("example.com")

    assert results == []


async def test_discover_feeds_returns_empty_list_for_blank_input():
    results = await feed_discovery.discover_feeds("   ")

    assert results == []


async def test_discover_feeds_caps_at_max_results(httpx_mock):
    links = "".join(
        f'<link rel="alternate" type="application/rss+xml" href="https://example.com/feed{i}.xml">'
        for i in range(feed_discovery.MAX_RESULTS + 5)
    )
    html = f"<html><head>{links}</head><body></body></html>"
    httpx_mock.add_response(url="https://example.com", text=html)

    results = await feed_discovery.discover_feeds("example.com")

    assert len(results) == feed_discovery.MAX_RESULTS
