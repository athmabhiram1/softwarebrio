"""cleaner.py — regex pre-pass + trafilatura→BS4 + token-capped concat."""
from __future__ import annotations

import re
from urllib.parse import urlparse

import config

try:
    import trafilatura  # type: ignore
except Exception:  # pragma: no cover
    trafilatura = None  # type: ignore

try:
    from bs4 import BeautifulSoup, Comment  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    Comment = None  # type: ignore


def regex_prepass(raw_html: str) -> dict:
    li_pat = re.compile(r"linkedin\.com/(?:in|company)/[\w-]+", re.I)
    mailto_pat = re.compile(r"mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.I)
    email_pat = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    seen_li: set[str] = set()
    linkedin_urls: list[str] = []
    for m in li_pat.finditer(raw_html):
        u = m.group(0)
        norm = u if u.lower().startswith("https://") else f"https://{u}"
        if norm not in seen_li:
            seen_li.add(norm)
            linkedin_urls.append(norm)
    seen_em: set[str] = set()
    emails: list[str] = []
    # collect in order of appearance across both patterns
    candidates: list[tuple[int, str]] = []
    for m in mailto_pat.finditer(raw_html):
        candidates.append((m.start(1), m.group(1).lower()))
    for m in email_pat.finditer(raw_html):
        candidates.append((m.start(0), m.group(0).lower()))
    candidates.sort(key=lambda x: x[0])
    for _, e in candidates:
        if e not in seen_em:
            seen_em.add(e)
            emails.append(e)
    return {"linkedin_urls": linkedin_urls, "emails": emails}


def clean_page(html: str) -> dict:
    raw_len = len(html)
    text: str | None = None
    path = "bs4"
    if trafilatura is not None:
        try:
            t = trafilatura.extract(html, output_format="txt", include_comments=False, include_tables=False, favor_precision=True)
            if t and t.strip():
                text = t.strip()
                path = "trafilatura"
            else:
                t2 = trafilatura.extract(html, output_format="txt", include_comments=False, include_tables=False, favor_recall=True)
                if t2 and t2.strip():
                    text = t2.strip()
                    path = "trafilatura-recall"
        except Exception:
            text = None
    if text is None:
        if BeautifulSoup is None:
            text = ""
        else:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "svg", "nav", "footer", "header", "aside", "form", "noscript", "iframe"]):
                tag.decompose()
            if Comment is not None:
                for c in soup.find_all(string=lambda x: isinstance(x, Comment)):
                    c.extract()
            text = soup.get_text(separator="\n", strip=True) or ""
            path = "bs4"
    clean_len = len(text)
    return {"text": text or "", "path": path, "raw_len": raw_len, "clean_len": clean_len}


def estimate_tokens(text: str) -> int:
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def _priority_index(url: str) -> int:
    # Accept schemeless keys ("postman.com", "postman.com/pricing") as well
    # as absolute URLs; a bare domain counts as the homepage.
    u = url if "://" in url else "https://" + url
    p = urlparse(u).path
    if p in ("", "/"):
        try:
            return config.PAGE_PRIORITY.index("homepage")
        except ValueError:
            return len(config.PAGE_PRIORITY)
    low = url.lower()
    for i, tok in enumerate(config.PAGE_PRIORITY):
        if tok == "homepage":
            continue
        if re.search(tok, low):
            return i
    return len(config.PAGE_PRIORITY)


def concat_domain(pages: dict[str, str]) -> dict:
    found_li: list[str] = []
    found_em: list[str] = []
    seen_li: set[str] = set()
    seen_em: set[str] = set()
    cleaned: dict[str, str] = {}
    for url, html in pages.items():
        pre = regex_prepass(html)
        for u in pre["linkedin_urls"]:
            if u not in seen_li:
                seen_li.add(u); found_li.append(u)
        for e in pre["emails"]:
            le = e.lower()
            if le not in seen_em:
                seen_em.add(le); found_em.append(le)
        cleaned[url] = clean_page(html)["text"] or ""
    for _ in range(200):
        combined = "\n\n".join(v for v in cleaned.values() if v)
        if estimate_tokens(combined) <= config.TOKEN_BUDGET or not any(v.strip() for v in cleaned.values() if v):
            break
        cands = [(u, t) for u, t in cleaned.items() if t.strip()]
        if not cands:
            break
        victim = max(cands, key=lambda kv: _priority_index(kv[0]))[0]
        txt = cleaned[victim]
        new_len = int(len(txt) * 0.8)
        if new_len < 100:
            cleaned[victim] = ""; continue
        cut = txt[:new_len]
        ws = cut.rfind(" ")
        if ws == -1:
            ws = cut.rfind("\n")
        if ws > 100:
            cut = cut[:ws]
        cleaned[victim] = "" if len(cut.strip()) < 100 else cut
    final = "\n\n".join(v for v in cleaned.values() if v)
    return {"text": final, "found_linkedin_urls": found_li, "found_emails": found_em}
