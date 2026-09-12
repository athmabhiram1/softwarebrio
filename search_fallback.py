"""search_fallback.py — Tavily LinkedIn fallback for empty/company-only leadership."""
from __future__ import annotations

import logging
import re
import time

import config
import httpx
from extractor import compute_confidence
from models import CompanyRecord, LeadershipEntry

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"

_PROV_VALUES: dict[str, float] = {
    "regex": 1.0,
    "inferred": 0.5,
    "llm_inferred": 0.5,
    "search_fallback": 0.25,
    "none": 0.0,
}


def should_trigger(rec: CompanyRecord) -> bool:
    if not rec.leadership:
        return True
    for le in rec.leadership:
        try:
            url = (le.linkedin_url or "").lower()
        except Exception:
            continue
        if url and "linkedin.com/company/" not in url:
            return False
    return True


def _normalize_name(n: str) -> str:
    try:
        s = n.casefold().strip()
    except Exception:
        return ""
    try:
        s = re.sub(r"[^a-z0-9\s]", "", s)
    except Exception:
        return ""
    try:
        s = re.sub(r"\s+", " ", s).strip()
    except Exception:
        return ""
    return s


_NON_PERSON_TOKENS = frozenset(
    {
        "pricing", "jobs", "job", "login", "signin", "signup", "careers",
        "career", "company", "companies", "home", "feed", "network",
        "premium", "learning", "business", "talent", "hiring", "apply",
        "discover", "join",
    }
)

_CREDENTIAL_RE = re.compile(
    r",\s*(Ph\.?\s?D\.?|M\.?\s?B\.?\s?A\.?|C\.?\s?P\.?\s?A\.?|M\.?\s?D\.?|J\.?\s?D\.?|Esq\.?|Jr\.?|Sr\.?|III|II|IV)\.?$",
    re.IGNORECASE,
)


def _parse_bare_name(text: str) -> tuple[str, str] | None:
    """Bare LinkedIn display name (no dash/pipe), title unknown -> (name, "")."""
    s = (text or "").strip()
    if not s or "|" in s or " - " in s:
        return None
    try:
        s = _CREDENTIAL_RE.sub("", s).strip()
    except Exception:
        return None
    words = s.split()
    if len(words) < 2:
        return None
    try:
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", w) for w in words):
            return None
    except Exception:
        return None
    try:
        if any(w.lower() in _NON_PERSON_TOKENS for w in words):
            return None
    except Exception:
        return None
    return s, ""


def _parse_single(text: str) -> tuple[str, str] | None:
    if not text:
        return None
    if "|" not in text:
        return _parse_bare_name(text)
    left = text.split("|", 1)[0].strip()
    if " - " not in left:
        return _parse_bare_name(left)
    name_part, title_part = left.split(" - ", 1)
    name = name_part.strip()
    title_raw = title_part.strip()
    try:
        title = re.split(r"\s+at\s+|\s+@\s+", title_raw, maxsplit=1)[0].strip()
    except Exception:
        return None
    if not name or not title:
        return None
    words = name.split()
    if len(words) < 2:
        return None
    try:
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", w) for w in words):
            return None
    except Exception:
        return None
    return name, title


def parse_name_title(title: str, content: str) -> tuple[str, str] | None:
    for text in (title, content):
        try:
            parsed = _parse_single(text)
        except Exception:
            continue
        if parsed is not None:
            return parsed
    return None


def extract_linkedin_profiles(
    results: list[dict],
) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for r in results or []:
        if len(out) >= 3:
            break
        try:
            url = str(r.get("url", ""))
        except Exception:
            continue
        if "/company/" in url.lower():
            continue
        if "/in/" not in url.lower():
            continue
        try:
            title = str(r.get("title", ""))
            content = str(r.get("content", ""))
        except Exception:
            continue
        try:
            parsed = parse_name_title(title, content)
        except Exception:
            continue
        if parsed is None:
            continue
        name, ptitle = parsed
        if not name.strip():
            continue
        out.append((url, name.strip(), ptitle.strip()))
    return out


def _is_429_exc(exc: Exception) -> bool:
    try:
        resp = getattr(exc, "response", None)
        if resp is not None and getattr(resp, "status_code", None) == 429:
            return True
    except Exception:
        pass
    if getattr(exc, "status_code", None) == 429:
        return True
    if "429" in str(exc):
        return True
    return False


def search_tavily(domain: str) -> tuple[list[dict], dict]:
    template = getattr(
        config, "TAVILY_QUERY_TEMPLATE", "{domain} founder OR CEO OR co-founder linkedin"
    )
    try:
        query = template.format(domain=domain)
    except Exception:
        query = f"{domain} founder OR CEO OR co-founder linkedin"
    depth = getattr(config, "TAVILY_SEARCH_DEPTH", "basic")
    max_results = getattr(config, "TAVILY_MAX_RESULTS", 5)
    timeout = getattr(config, "TAVILY_TIMEOUT_S", 10)
    backoff = getattr(config, "RATE_LIMIT_BACKOFF_S", 5)
    api_key = getattr(config, "TAVILY_API_KEY", None)
    enabled: bool = getattr(config, "TAVILY_ENABLED", True)
    include_domains: list[str] = getattr(config, "TAVILY_INCLUDE_DOMAINS", ["linkedin.com"])
    include_domains_mode: str = getattr(config, "TAVILY_INCLUDE_DOMAINS_MODE", "filter")
    meta: dict = {
        "tavily_calls": 0,
        "tavily_credits": 0,
        "tavily_status": None,
        "tavily_query": query,
    }
    if not api_key or not enabled:
        return [], meta
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {
        "query": query,
        "search_depth": depth,
        "max_results": max_results,
        "include_answer": False,
        "include_domains": include_domains,
        "include_domains_mode": include_domains_mode,
    }
    for attempt in range(3):
        try:
            resp = httpx.post(TAVILY_URL, json=body, headers=headers, timeout=timeout)
        except Exception as e:
            if _is_429_exc(e) and attempt < 2:
                try:
                    time.sleep(backoff * (2**attempt))
                except Exception:
                    pass
                continue
            logger.warning(
                "tavily search failed domain=%s type=%s status=%s",
                domain,
                type(e).__name__,
                getattr(getattr(e, "response", None), "status_code", None),
            )
            break
        try:
            status = resp.status_code
        except Exception:
            status = None
        if status == 429 and attempt < 2:
            try:
                time.sleep(backoff * (2**attempt))
            except Exception:
                pass
            continue
        meta["tavily_status"] = status
        if status != 200:
            logger.warning(
                "tavily search failed domain=%s type=%s status=%s",
                domain,
                "HTTPError",
                status,
            )
            break
        try:
            data = resp.json()
        except Exception as e:
            logger.warning(
                "tavily search failed domain=%s type=%s status=%s",
                domain,
                type(e).__name__,
                status,
            )
            break
        results = data.get("results", []) if isinstance(data, dict) else []
        meta.update({"tavily_calls": 1, "tavily_credits": 1})
        return list(results), meta
    return [], meta


def _seed_sources(
    rec: CompanyRecord, found_li: list[str], sources: dict[str, str]
) -> None:
    try:
        found = set(found_li or [])
    except Exception:
        found = set()
    try:
        existing = getattr(rec, "leadership_sources", {}) or {}
        for k, v in existing.items():
            sources[str(k)] = str(v)
    except Exception:
        pass
    for le in rec.leadership or []:
        try:
            url = le.linkedin_url or ""
            if url and url not in sources:
                sources[url] = "regex" if url in found else "llm_inferred"
        except Exception:
            continue


def _apply_candidates(
    rec: CompanyRecord,
    candidates: list[tuple[str, str, str]],
    sources: dict[str, str],
) -> tuple[int, int]:
    norm_index: dict[str, int] = {}
    for i, le in enumerate(rec.leadership or []):
        try:
            norm_index[_normalize_name(le.name or "")] = i
        except Exception:
            continue
    appended = 0
    backfilled = 0
    for cand in candidates or []:
        try:
            url, name, title = cand
        except Exception:
            continue
        if not (name or "").strip():
            continue
        key = _normalize_name(name)
        if not key:
            continue
        if key in norm_index:
            idx = norm_index[key]
            try:
                if not (rec.leadership[idx].linkedin_url or "").strip():
                    rec.leadership[idx].linkedin_url = (url or "").strip()
                    backfilled += 1
                if not (rec.leadership[idx].title or "").strip() and (title or "").strip():
                    rec.leadership[idx].title = title.strip()
                if url:
                    sources[url] = "search_fallback"
            except Exception:
                continue
        else:
            try:
                rec.leadership.append(
                    LeadershipEntry(
                        name=name.strip(),
                        title=(title or "").strip(),
                        linkedin_url=(url or "").strip(),
                    )
                )
            except Exception:
                continue
            norm_index[key] = len(rec.leadership) - 1
            if url:
                sources[url] = "search_fallback"
            appended += 1
    return appended, backfilled


def _recompute_confidence(rec: CompanyRecord, sources: dict[str, str]) -> None:
    # C = max(provenance value across all leadership entries)
    vals: list[float] = []
    for le in rec.leadership or []:
        try:
            url = le.linkedin_url or ""
            vals.append(_PROV_VALUES.get(sources.get(url, "none"), 0.0))
        except Exception:
            continue
    c_max = max(vals) if vals else 0.0
    if c_max >= 1.0:
        contact_mode = "regex"
    elif c_max >= 0.5:
        contact_mode = "inferred"
    elif c_max > 0.0:
        contact_mode = "search_fallback"
    else:
        contact_mode = "none"
    try:
        f_pop = (
            int(bool((rec.company_overview or "").strip()))
            + int(bool((rec.target_audience or "").strip()))
            + int(len(rec.contact_points or []) > 0)
            + int(len(rec.leadership or []) > 0)
        )
    except Exception:
        f_pop = int(len(rec.leadership or []) > 0)
    try:
        new_conf = compute_confidence(f_pop, 0, 0, contact_mode, 0)
    except Exception:
        return
    try:
        rec.confidence_score = max(0.05, float(new_conf)) if rec.leadership else float(new_conf)
    except Exception:
        pass


def enrich_record(
    rec: CompanyRecord,
    candidates: list[tuple[str, str, str]],
    found_li: list[str],
) -> tuple[CompanyRecord, dict]:
    sources: dict[str, str] = {}
    _seed_sources(rec, found_li, sources)
    appended, backfilled = _apply_candidates(rec, candidates, sources)
    _recompute_confidence(rec, sources)
    if appended or backfilled:
        try:
            rec.errors.append(
                f"search_fallback: appended={appended} backfilled={backfilled}"
            )
        except Exception:
            pass
    try:
        rec.leadership_sources = sources  # type: ignore[attr-defined]
    except Exception:
        try:
            object.__setattr__(rec, "leadership_sources", sources)
        except Exception:
            pass
    return rec, sources


__all__ = [
    "should_trigger",
    "parse_name_title",
    "extract_linkedin_profiles",
    "search_tavily",
    "enrich_record",
    "compute_confidence",
]
