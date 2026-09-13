"""main.py - CLI batch orchestration (Todo 6)."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime

import config
import cleaner
import fetcher
import extractor
import search_fallback
from models import CompanyRecord

logger = logging.getLogger(__name__)


def _parse_domains(value: str) -> list[str]:
    if value is None or value.strip() == "":
        raise argparse.ArgumentTypeError("domains cannot be empty")
    parts = [d.strip().lower() for d in value.split(",") if d.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("domains cannot be empty")
    return parts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lead enrichment batch")
    parser.add_argument(
        "--domains",
        type=_parse_domains,
        default=config.TARGET_DOMAINS,
        help="comma-separated domains",
    )
    parser.add_argument("--out", type=str, default=config.OUTPUT_PATH, help="output path")
    parser.add_argument("--provider", type=str, default=config.MODEL_PROVIDER, help="model provider")
    args = parser.parse_args(argv)
    # argparse default is list - validate not empty
    if isinstance(args.domains, list):
        if len(args.domains) == 0:
            parser.error("--domains cannot be empty")
    elif not args.domains:
        parser.error("--domains cannot be empty")
    # provider override validation
    if not str(args.provider).strip():
        parser.error("--provider cannot be empty")
    return args


def _compute_degraded(pages: dict[str, str], methods: dict[str, str]) -> int:
    # Choice: clean each page once via clean_page, remember path,
    # degraded = (any path != trafilatura ?1:0)+(any method==playwright?1:0) capped at 2.
    # This re-derives clean paths without needing fetcher to return them.
    fallback = 0
    for url, html in pages.items():
        try:
            cr = cleaner.clean_page(html)
            if cr.get("path") != "trafilatura":
                fallback = 1
                break
        except Exception:
            fallback = 1
            break
    playwright_flag = 1 if any(v == "playwright" for v in methods.values()) else 0
    deg = fallback + playwright_flag
    return 2 if deg > 2 else deg


def _mix_label(methods: dict[str, str]) -> str:
    return "httpx+playwright" if any(v == "playwright" for v in methods.values()) else "httpx"


def _calc_pages_ok(pages: dict[str, str], errors_fetch: list[str]) -> tuple[int, int]:
    total = len(pages)
    if not errors_fetch:
        return total, total
    err_urls = {u for u in pages if any(u in e for e in errors_fetch)}
    ok = total - (len(err_urls) if err_urls else len(errors_fetch))
    ok = max(0, min(total, ok))
    return ok, total


async def _process_single(domain: str, idx: int, n: int) -> tuple[CompanyRecord, int, float]:
    t0 = time.perf_counter()
    fetched = await fetcher.fetch_domain(domain, client=None)
    pages: dict[str, str] = fetched.get("pages", {})
    methods: dict[str, str] = fetched.get("methods", {})
    errors_fetch: list[str] = fetched.get("errors", [])
    raw_kb = sum(len(h) for h in pages.values()) / 1024
    degraded = _compute_degraded(pages, methods)
    mix = _mix_label(methods)
    pages_ok, pages_total = _calc_pages_ok(pages, errors_fetch)
    concat = cleaner.concat_domain(pages)
    cleaned_text: str = concat.get("text", "")
    found_li: list[str] = concat.get("found_linkedin_urls", [])
    found_em: list[str] = concat.get("found_emails", [])
    if not cleaned_text.strip():
        rec = CompanyRecord(company_overview="", target_audience="", contact_points=[], leadership=[], confidence_score=0.05, errors=[*errors_fetch] if errors_fetch else ["no usable cleaned text after fetch/clean"])
        sec = time.perf_counter() - t0
        print(f"[{idx}/{n}] {domain}: {mix} | raw {raw_kb:.1f}KB → clean 0.0KB (~0 tok) | LLM 0/0 tok $0.0000 | conf 0.05 | {sec:.1f}s | Tavily 0/0c")
        try:
            object.__setattr__(rec, "_tavily_calls", 0)
            object.__setattr__(rec, "_tavily_credits", 0)
        except Exception:
            pass
        return rec, 0, 0.0
    clean_kb = len(cleaned_text) / 1024
    est_tok = cleaner.estimate_tokens(cleaned_text)
    try:
        rec, meta = extractor.extract_domain(domain, cleaned_text, found_li, found_em, pages_ok, pages_total, degraded=degraded)
    except Exception as e:
        rec = CompanyRecord(company_overview="", target_audience="", contact_points=[], leadership=[], confidence_score=0.05, errors=[*errors_fetch, f"{type(e).__name__}: {e}"])
        model = config.GROQ_MODEL if config.MODEL_PROVIDER == "groq" else config.OLLAMA_MODEL
        meta = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "model": model}
    tavily_calls = 0
    tavily_credits = 0
    tavily_status: int | None = None
    try:
        if config.TAVILY_API_KEY and search_fallback.should_trigger(rec):
            _results, _tmeta = search_fallback.search_tavily(domain)
            tavily_calls = int(_tmeta.get("tavily_calls", 0))
            tavily_credits = int(_tmeta.get("tavily_credits", 0))
            tavily_status = _tmeta.get("tavily_status")
            meta.update(_tmeta)
            _cands = search_fallback.extract_linkedin_profiles(_results)
            if _cands:
                rec, _sources = search_fallback.enrich_record(rec, _cands, found_li)
    except Exception as e:
        logger.warning("search_fallback failed domain=%s type=%s", domain, type(e).__name__)
        rec.errors.append(f"search_fallback failed: {type(e).__name__}: {e}")
    pt = int(meta.get("prompt_tokens", 0))
    ct = int(meta.get("completion_tokens", 0))
    cost = float(meta.get("cost_usd", 0.0))
    sec = time.perf_counter() - t0
    print(f"[{idx}/{n}] {domain}: {mix} | raw {raw_kb:.1f}KB → clean {clean_kb:.1f}KB (~{est_tok} tok) | LLM {pt}/{ct} tok ${cost:.4f} | conf {rec.confidence_score:.2f} | {sec:.1f}s | Tavily {tavily_calls}/{tavily_credits}c")
    try:
        object.__setattr__(rec, "_tavily_calls", tavily_calls)
        object.__setattr__(rec, "_tavily_credits", tavily_credits)
    except Exception:
        pass
    return rec, est_tok + pt + ct, cost


_STABLE_OUT_NAMES = frozenset({"output.json", "output.local.json"})


def _resolve_out_path(out_path: str, now: datetime | None = None) -> str:
    norm = out_path.replace("\\", "/")
    if "/" not in norm and norm.rsplit("/", 1)[-1] in _STABLE_OUT_NAMES:
        try:
            os.makedirs("output", exist_ok=True)
        except Exception:
            pass
        out_path = f"output/{norm}"
    base = out_path.replace("\\", "/").rsplit("/", 1)[-1]
    if base not in _STABLE_OUT_NAMES:
        return out_path
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    stem, dot, ext = out_path.rpartition(".")
    if not dot:
        return f"{out_path}-{stamp}"
    return f"{stem}-{stamp}{dot}{ext}"


async def run_batch(domains: list[str], out_path: str, provider: str | None = None) -> dict:
    if provider is not None:
        config.MODEL_PROVIDER = provider.strip().lower()
    out_path = _resolve_out_path(out_path)
    n = len(domains)
    records: list[CompanyRecord] = []
    total_tokens = 0
    total_cost = 0.0
    domains_failed = 0
    tavily_calls_total = 0
    tavily_credits_total = 0
    batch_start = time.perf_counter()
    for idx, domain in enumerate(domains, start=1):
        t0 = time.perf_counter()
        try:
            rec, tok, cost = await _process_single(domain, idx, n)
            records.append(rec)
            total_tokens += tok
            total_cost += cost
            try:
                tavily_calls_total += int(getattr(rec, "_tavily_calls", 0) or 0)
                tavily_credits_total += int(getattr(rec, "_tavily_credits", 0) or 0)
            except Exception:
                pass
            if not rec.company_overview.strip() and not rec.target_audience.strip() and not rec.contact_points and not any(le.name.strip() or le.title.strip() or le.linkedin_url.strip() for le in rec.leadership):
                domains_failed += 1
        except Exception as e:
            sec = time.perf_counter() - t0
            rec = CompanyRecord(company_overview="", target_audience="", contact_points=[], leadership=[], confidence_score=0.05, errors=[str(e)])
            records.append(rec)
            domains_failed += 1
            print(f"[{idx}/{n}] {domain}: httpx | raw 0.0KB → clean 0.0KB (~0 tok) | LLM 0/0 tok $0.0000 | conf 0.05 | {sec:.1f}s | Tavily 0/0c")
    total_runtime = time.perf_counter() - batch_start
    summary = {"total_tokens": total_tokens, "total_cost_usd": round(total_cost, 6), "total_runtime_s": round(total_runtime, 2), "domains_failed": domains_failed, "tavily_calls": tavily_calls_total, "tavily_credits": tavily_credits_total}
    out_data = {"records": [r.model_dump() for r in records], "summary": summary}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)
    return out_data


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    out = args.out
    domains = args.domains if isinstance(args.domains, list) else _parse_domains(str(args.domains))
    # handle empty after parse (argparse already exited 2)
    asyncio.run(run_batch(domains, out, provider=str(args.provider)))


if __name__ == "__main__":
    main()
