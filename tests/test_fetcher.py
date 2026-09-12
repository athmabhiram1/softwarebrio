import pytest
import httpx
import config
import fetcher
from cleaner import clean_page

# ---- fixtures ----
RICH_HTML = """
<html><head><title>Postman Test</title></head><body>
<article>
<h1>Postman is a collaboration platform for API development</h1>
<p>Postman is a collaboration platform for API development. It simplifies every step of building an API and streamlines collaboration.</p>
<p>Developers use Postman to design, test, and document APIs quickly. Teams collaborate across workspaces with version control and CI integration.</p>
<p>Our mission is to make API development accessible to everyone worldwide with powerful tooling and integrations.</p>
<p>Postman supports collections, environments, and automated testing workflows for thousands of teams globally.</p>
<p>Founded in 2014, Postman has grown to serve millions of developers and enterprise customers.</p>
<p>Extra paragraph to ensure cleaned text exceeds five hundred characters threshold for escalation logic verification.</p>
<p>Another sentence with meaningful content about APIs, testing, automation, and collaboration at scale for modern teams.</p>
</article>
</body></html>
"""

THIN_HTML = "<html><head></head><body><div id='root'></div><script>renderApp();</script></body></html>"

# verify fixture sizes for debugging
rich_clean = clean_page(RICH_HTML)["text"]
thin_clean = clean_page(THIN_HTML)["text"]


def test_needs_playwright_table():
    # 200 + rich -> False
    assert fetcher.needs_playwright(200, RICH_HTML) is False, f"rich len {len(rich_clean.strip())}"
    # 200 + thin (<500 cleaned) -> True
    assert fetcher.needs_playwright(200, THIN_HTML) is True, f"thin len {len(thin_clean.strip())}"
    # 403 -> True regardless
    assert fetcher.needs_playwright(403, RICH_HTML) is True
    # 404 -> False never escalate
    assert fetcher.needs_playwright(404, RICH_HTML) is False
    assert fetcher.needs_playwright(404, THIN_HTML) is False
    # 500 -> True
    assert fetcher.needs_playwright(500, RICH_HTML) is True
    # 429 -> True
    assert fetcher.needs_playwright(429, RICH_HTML) is True
    # None -> True
    assert fetcher.needs_playwright(None, RICH_HTML) is True
    # status None thin also True
    assert fetcher.needs_playwright(None, THIN_HTML) is True
    print(f"needs_playwright: rich_clean={len(rich_clean.strip())} thin_clean={len(thin_clean.strip())} ESCALATE={config.ESCALATE_MIN_CLEAN_CHARS}")


def test_discover_subpages():
    base = "https://example.com"
    html = """
    <a href="/about">About</a>
    <a href="/team">Team</a>
    <a href="https://example.com/contact">Contact</a>
    <a href="/pricing">Pricing</a>
    <a href="/company">Company</a>
    <a href="/about">About dup</a>
    <a href="https://evil.com/about">External</a>
    <a href="/blog">Blog no-match</a>
    <a href="/leadership">Leadership</a>
    <a href="/about?x=1">About query</a>
    """
    result = fetcher.discover_subpages(html, base)
    # externals dropped
    assert all("evil.com" not in u for u in result)
    # non-matching /blog dropped
    assert all("/blog" not in u for u in result)
    # duplicate removed, order preserved, cap 5
    assert len(result) <= config.MAX_SUBPAGES
    assert len(result) == len(set(result))
    # first should be /about
    assert result[0] == "https://example.com/about"
    # should preserve order: about, team, contact, pricing, company (5th)
    assert result == [
        "https://example.com/about",
        "https://example.com/team",
        "https://example.com/contact",
        "https://example.com/pricing",
        "https://example.com/company",
    ]
    # leadership would be 6th but capped
    assert "https://example.com/leadership" not in result
    print(f"discover_subpages result={result}")


# ---- fake client helpers ----
class FakeResp:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text
        self.request = None
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"status {self.status_code}", request=httpx.Request("GET", "https://example.com"), response=self)  # type: ignore


class FakeClient:
    def __init__(self, mapping: dict[str, tuple[int, str]]):
        self.mapping = mapping
        self.calls: list[str] = []

    async def get(self, url: str):
        self.calls.append(url)
        # strip fragment for lookup
        key = url.split("#")[0]
        if key in self.mapping:
            sc, txt = self.mapping[key]
            return FakeResp(sc, txt)
        # default 404
        return FakeResp(404, "")

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_fetch_domain_methods_and_single_browser(monkeypatch):
    # homepage rich (no escalation), one subpage thin (escalates)
    homepage = "https://example.com"
    sub = "https://example.com/about"
    home_html = '<html><body>' + RICH_HTML + f'<a href="/about">About</a></body></html>'
    mapping = {
        homepage: (200, home_html),
        sub: (200, THIN_HTML),
    }
    fake_client = FakeClient(mapping)
    launch_counter = {"n": 0}

    class FakePage:
        async def goto(self, url, wait_until="domcontentloaded", timeout=10000):
            assert wait_until == "domcontentloaded"
            assert timeout == 10000
            assert wait_until != "networkidle"
            class R:
                status = 200
            return R()

        async def content(self):
            return "<html><body>playwright rendered rich content " + ("word " * 200) + "</body></html>"

        async def close(self):
            pass

    class FakeBrowser:
        async def new_page(self):
            return FakePage()

    async def fake_ensure():
        launch_counter["n"] += 1
        return FakeBrowser()

    monkeypatch.setattr(fetcher, "ensure_browser", fake_ensure)

    # stub fetch_page_playwright to ensure it uses domcontentloaded (checked above) but also wrap original?
    # Use real fetch_page_playwright which will call FakeBrowser.new_page
    # No need to stub fetch_page_playwright further

    result = await fetcher.fetch_domain("example.com", client=fake_client)  # type: ignore
    assert result["pages"][homepage] is not None
    # homepage should be httpx (rich)
    assert result["methods"][homepage] == "httpx", result["methods"]
    # subpage thin should escalate to playwright
    assert result["methods"][sub] == "playwright", result["methods"]
    assert launch_counter["n"] == 1, f"expected single launch got {launch_counter['n']}"
    print(f"fetch_domain methods={result['methods']} launch={launch_counter['n']}")


@pytest.mark.asyncio
async def test_fetch_domain_404_no_escalation(monkeypatch):
    homepage = "https://example.com"
    sub404 = "https://example.com/about"
    home_html = '<html><body>' + RICH_HTML + f'<a href="/about">About</a></body></html>'
    mapping = {
        homepage: (200, home_html),
        sub404: (404, "not found"),
    }
    fake_client = FakeClient(mapping)
    called = {"playwright": False}

    async def fake_ensure2():
        called["playwright"] = True
        raise AssertionError("should not escalate 404")

    monkeypatch.setattr(fetcher, "ensure_browser", fake_ensure2)
    # also ensure fetch_page_playwright not called
    orig_pw = fetcher.fetch_page_playwright

    async def guard_pw(url, browser):
        called["playwright"] = True
        raise AssertionError("playwright called for 404")

    monkeypatch.setattr(fetcher, "fetch_page_playwright", guard_pw)

    result = await fetcher.fetch_domain("example.com", client=fake_client)  # type: ignore
    assert sub404 in result["pages"]
    assert result["methods"][sub404] == "httpx"
    assert any("404" in e for e in result["errors"]), result["errors"]
    assert called["playwright"] is False, "escalation should not happen for 404"
    print(f"404 errors={result['errors']} methods={result['methods']}")
    monkeypatch.setattr(fetcher, "fetch_page_playwright", orig_pw)
