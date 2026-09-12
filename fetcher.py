"""fetcher.py — httpx-first + conditional single-browser Playwright."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

if TYPE_CHECKING:
    from playwright.async_api import Browser

import httpx

import config
from cleaner import clean_page

logger = logging.getLogger(__name__)

_browser = None
_playwright = None


async def fetch_page_httpx(url: str, client: httpx.AsyncClient) -> dict:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return {"url": url, "status": resp.status_code, "html": resp.text, "error": None}
    except httpx.TimeoutException as e:
        logger.warning("httpx TimeoutException %s: %s url=%s", type(e).__name__, e, url)
        return {"url": url, "status": None, "html": "", "error": f"{type(e).__name__}: {e}"}
    except httpx.HTTPStatusError as e:
        st = e.response.status_code if e.response is not None else None
        html = e.response.text if e.response is not None else ""
        logger.warning("httpx HTTPStatusError %s: %s url=%s status=%s", type(e).__name__, e, url, st)
        return {"url": url, "status": st, "html": html, "error": f"{type(e).__name__}: {e}"}
    except httpx.RequestError as e:
        logger.warning("httpx RequestError %s: %s url=%s", type(e).__name__, e, url)
        return {"url": url, "status": None, "html": "", "error": f"{type(e).__name__}: {e}"}


def needs_playwright(status: int | None, html: str) -> bool:
    if status == 404:
        return False
    if status in (403, 429):
        return True
    if status is None:
        return True
    if status >= 500:
        return True
    cleaned = clean_page(html)["text"]
    if len(cleaned.strip()) < config.ESCALATE_MIN_CLEAN_CHARS:
        return True
    return False


async def fetch_page_playwright(url: str, browser: Browser) -> dict:
    page = None
    try:
        page = await browser.new_page()
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=10000)
        html = await page.content()
        st = resp.status if resp is not None else None
        return {"url": url, "status": st, "html": html, "error": None}
    except Exception as e:
        logger.warning("playwright error %s: %s url=%s", type(e).__name__, e, url)
        return {"url": url, "status": None, "html": "", "error": f"{type(e).__name__}: {e}"}
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


def discover_subpages(homepage_html: str, base_url: str) -> list[str]:
    hrefs = re.findall(r'href=["\']([^"\']+)', homepage_html)
    base_netloc = urlparse(base_url).netloc
    seen: set[str] = set()
    out: list[str] = []
    for h in hrefs:
        abs_url = urljoin(base_url, h)
        parsed = urlparse(abs_url)
        if parsed.netloc != base_netloc:
            continue
        if not config.SUBPAGE_RE.search(parsed.path):
            continue
        norm = abs_url.split("#")[0]
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
        if len(out) >= config.MAX_SUBPAGES:
            break
    return out


async def ensure_browser() -> Browser:
    global _browser, _playwright
    if _browser is not None:
        return _browser
    from playwright.async_api import async_playwright

    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch()
    return _browser


async def close_browser() -> None:
    global _browser, _playwright
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None


async def _fetch_one(url: str, client: httpx.AsyncClient, errors: list[str]) -> tuple[str, str]:
    r = await fetch_page_httpx(url, client)
    html, status = r["html"], r["status"]
    if status == 404:
        errors.append(f"{url} 404")
        logger.info("%s -> httpx (404)", url)
        return html, "httpx"
    if r["error"]:
        errors.append(f"{url} {r['error']}")
    if needs_playwright(status, html):
        br = await ensure_browser()
        pw = await fetch_page_playwright(url, br)
        if pw["error"]:
            errors.append(f"{url} playwright {pw['error']}")
        html = pw["html"] if pw["html"] else html
        logger.info("%s -> playwright", url)
        return html, "playwright"
    logger.info("%s -> httpx", url)
    return html, "httpx"


async def fetch_domain(domain: str, client: httpx.AsyncClient | None = None) -> dict:
    dom = domain.strip()
    if dom.startswith("http://"):
        dom = dom[7:]
    elif dom.startswith("https://"):
        dom = dom[8:]
    dom = dom.rstrip("/")
    homepage_url = f"https://{dom}"
    pages: dict[str, str] = {}
    methods: dict[str, str] = {}
    errors: list[str] = []
    owned = False
    if client is None:
        client = httpx.AsyncClient(headers={"User-Agent": config.BROWSER_USER_AGENT}, timeout=config.FETCH_TIMEOUT_S, follow_redirects=True)
        owned = True
    try:
        html, meth = await _fetch_one(homepage_url, client, errors)
        pages[homepage_url] = html
        methods[homepage_url] = meth
        for sub_url in discover_subpages(html, homepage_url):
            sh, sm = await _fetch_one(sub_url, client, errors)
            pages[sub_url] = sh
            methods[sub_url] = sm
    finally:
        if owned:
            try:
                await client.aclose()
            except Exception:
                pass
    return {"domain": dom, "pages": pages, "methods": methods, "errors": errors}
