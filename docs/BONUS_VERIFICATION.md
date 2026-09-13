# BONUS_VERIFICATION.md — Tavily fallback end-to-end verification (read-only)

Branch: `bonus` @ `45988e4`. Date: 2026-09-12. No code was changed to produce
this file. `main` untouched throughout (HEAD `c5c9606` before, during, after).

## 0. Precondition + .gitignore drift resolution

Pre-run check:

```
bonus
---BONUS-HEAD---
dbea4f5 docs: bonus plan, LangChain NOT-BUILT seam and README note
---MAIN-HEAD---
c5c9606 fix: email-only contacts, name-plausibility check, domain threading, hardened sample
---STATUS---
## bonus...origin/bonus
 M .gitignore
?? .omo/
?? output/compose.log
?? output/compose_live.log
?? output/test_5domains.json
?? output/test_5domains.md
```

`.gitignore` drift was `+front1.html` (uncommitted). `front1.html` exists locally
and is a local-only reference doc exactly like the already-ignored `front.html`
(same precedent as commit `dc12354`). Decision: COMMITTED, not reverted, as
`45988e4 chore: ignore front1.html local-only reference doc` — matches the
established pattern and leaves no dangling modification.

## 1. Full pytest, TAVILY_ENABLED=1 with real key from .env

Command: `$env:TAVILY_ENABLED="1"; py -3.11 -m pytest -q`

Result: **55 passed, 1 failed in 22.54s.** The single failure is verbatim below.
It is the known live-key environment artifact (the fallback legitimately does its
job against faked extractor output — see analysis after the block), NOT a code
regression. Nothing was fixed per the read-only instruction.

```
....................................F...................
_____________________________ test_degraded_math ______________________________

tmp_path = WindowsPath('C:/Users/athma/AppData/Local/Temp/pytest-of-athma/pytest-38/test_degraded_math0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001D87E211110>

    def test_degraded_math(tmp_path, monkeypatch):
        import main
        import fetcher
        import extractor
        import cleaner
        from models import CompanyRecord

        # clean domain: trafilatura + httpx only
        # degraded domain: bs4 + playwright => degraded 2
        captured_degraded = {}

        async def fake_fetch(domain, client=None):
            html = "<html><body>hi</body></html>"
            url = f"https://{domain}"
            if domain == "clean.com":
                return {"domain": domain, "pages": {url: html}, "methods": {url: "httpx"}, "errors": []}
            else:
                # degraded: method playwright, path bs4
                return {"domain": domain, "pages": {url: html}, "methods": {url: "playwright"}, "errors": []}

        def fake_clean_bs4(html):
            return {"text": "short", "path": "bs4", "raw_len": len(html), "clean_len": 10}

        def fake_clean_traf(html):
            return {"text": "cleaned " * 30, "path": "trafilatura", "raw_len": len(html), "clean_len": 100}

        def fake_extract_capture(domain, cleaned_text, urls, emails, pages_ok, pages_total, degraded=0):
            captured_degraded[domain] = degraded
            # confidence reflects degraded: clean higher
            conf = 0.9 if degraded == 0 else 0.5 if degraded == 2 else 0.7
            rec = CompanyRecord(company_overview="ov", target_audience="aud", contact_points=[], leadership=[], confidence_score=conf, errors=[])
            meta = {"prompt_tokens": 5, "completion_tokens": 5, "cost_usd": 0.0001, "model": "m"}
            return rec, meta

        monkeypatch.setattr(fetcher, "fetch_domain", fake_fetch)
        # per-domain clean_page behavior
        def dispatch_clean(html):
            return {"text": "x", "path": "bs4", "raw_len": 10, "clean_len": 10}

        monkeypatch.setattr(extractor, "extract_domain", fake_extract_capture)
        monkeypatch.setattr(cleaner, "concat_domain", lambda pages: {"text": "cleaned", "found_linkedin_urls": [], "found_emails": []})
        monkeypatch.setattr(cleaner, "estimate_tokens", lambda text: 10)

        # test degraded computation directly
        # Simulate clean.com: page path trafilatura, method httpx => degraded 0
        monkeypatch.setattr(cleaner, "clean_page", lambda html: {"text": "t", "path": "trafilatura", "raw_len": 10, "clean_len": 10})
        out = tmp_path / "out_clean.json"
        import asyncio
        # clear captured
        captured_degraded.clear()
        asyncio.run(main.run_batch(["clean.com"], str(out), provider="groq"))
        conf_clean = json.loads(out.read_text())["records"][0]["confidence_score"]
        deg_clean = captured_degraded.get("clean.com")

        # now degraded.com: path bs4 + playwright => 2
        monkeypatch.setattr(cleaner, "clean_page", lambda html: {"text": "t", "path": "bs4", "raw_len": 10, "clean_len": 10})
        out2 = tmp_path / "out_degraded.json"
        captured_degraded.clear()
        asyncio.run(main.run_batch(["degraded.com"], str(out2), provider="groq"))
        conf_deg = json.loads(out2.read_text())["records"][0]["confidence_score"]
        deg_deg = captured_degraded.get("degraded.com")

        assert deg_clean == 0, f"expected 0 got {deg_clean}"
        assert deg_deg == 2, f"expected 2 got {deg_deg}"
>       assert conf_clean > conf_deg, f"clean {conf_clean} should > degraded {conf_deg}"
E       AssertionError: clean 0.49 should > degraded 0.5
E       assert 0.49 > 0.5

tests\test_main.py:162: AssertionError
---------------------------- Captured stdout call -----------------------------
[1/1] clean.com: httpx | raw 0.0KB → clean 0.0KB (~10 tok) | LLM 5/5 tok $0.0001 | conf 0.49 | 3.9s | Tavily 1/1c
[1/1] degraded.com: httpx+playwright | raw 0.0KB → clean 0.0KB (~10 tok) | LLM 5/5 tok $0.0001 | conf 0.50 | 4.6s | Tavily 1/1c
=========================== short test summary info ===========================
FAILED tests/test_main.py::test_degraded_math - AssertionError: clean 0.49 should > degraded 0.5
1 failed, 55 passed in 22.54s
```

Analysis (reported, not fixed): both synthetic domains return empty leadership,
so the fallback correctly triggers; with a real key present it performs live
Tavily searches, finds real candidates, and recomputes the faked 0.9/0.5
confidences (0.49 vs 0.50 from live data nondeterminism). In keyless grading-like
environments the hookup skips and this test passes (see §2). `TAVILY_ENABLED=0`
exists exactly so live-key checkouts can run hermetically.

## 2. Full pytest, TAVILY_ENABLED=0 (hermetic)

Command: `$env:TAVILY_ENABLED="0"; py -3.11 -m pytest -q`

Result (verbatim):

```
........................................................                 [100%]
56 passed in 2.80s
```

Zero-network evidence: 2.80s wall time vs 22.54s live-keyed (no per-call Tavily
latency possible in under 3s for 56 tests); the guard returns before
`httpx.post` is constructed (locked by
`test_search_tavily_disabled_skips_network`, which fails the run if post is ever
called while disabled). Keyless run (`.env` swapped out under try/finally,
restored verified) also gives `56 passed` — the grading-like contract is green
with existing tests unmodified.

## 3. Live run: docker compose up --build (postman.com, supabase.com, vapi.ai)

Build reused cached layers; container `lead-agent-1` exited with code 0.
Live print lines verbatim (Tavily segment on each):

```
lead-agent-1  | [1/3] postman.com: httpx+playwright | raw 2320.2KB → clean 24.4KB (~5788 tok) | LLM 6076/642 tok $0.0006 | conf 0.95 | 28.5s | Tavily 0/0c
lead-agent-1  | [2/3] supabase.com: httpx+playwright | raw 1925.7KB → clean 8.1KB (~2073 tok) | LLM 2405/829 tok $0.0004 | conf 0.84 | 16.2s | Tavily 1/1c
lead-agent-1  | [3/3] vapi.ai: httpx | raw 406.3KB → clean 2.8KB (~629 tok) | LLM 1002/663 tok $0.0003 | conf 0.64 | 16.2s | Tavily 1/1c
lead-agent-1 exited with code 0
```

(The trailing `RuntimeError: Event loop is closed` asyncio-shutdown noise after
the run is pre-existing container teardown chatter, exit code unaffected.)

Resulting `output.json` summary object verbatim:

```json
{
  "total_tokens": 20107,
  "total_cost_usd": 0.001351,
  "total_runtime_s": 60.85,
  "domains_failed": 0,
  "tavily_calls": 2,
  "tavily_credits": 2
}
```

## 4. Fallback records in full (supabase.com, vapi.ai)

supabase.com — fallback FIRED (Tavily 1/1c) but found no usable `/in/`
candidates in that hour's results, so the core record stands intact exactly as
designed (safe no-op, no crash, no invented people):

```json
{
  "company_overview": "Supabase is an open-source Firebase alternative that provides a fully managed Postgres database, authentication, storage, real-time, and edge functions, all accessible through a single platform. It offers a range of plans from free to enterprise, enabling developers to build, scale, and manage applications without leaving the dashboard.",
  "target_audience": "Supabase targets developers building web and mobile applications, from hobby projects to production-grade SaaS solutions.",
  "contact_points": [],
  "leadership": [
    {
      "name": "Jakob Steinn",
      "title": "Co-founder & Tech Lead",
      "linkedin_url": "https://linkedin.com/company/supabase"
    }
  ],
  "confidence_score": 0.84,
  "errors": [
    "company-page URL attributed to a person: Jakob Steinn"
  ],
  "domain": "supabase.com",
  "leadership_sources": {}
}
```

- `leadership_sources`: `{}` (enrich never ran — zero candidates, nothing to tag).
- `confidence_score`: 0.84 (core value untouched — no recompute without enrichment).
- `errors`: core company-page attribution note only; no `search_fallback` entry
  (nothing appended) and no failure entry (the search itself succeeded).

vapi.ai — fallback fired AND appended 2 real LinkedIn profiles end-to-end in the
production path (not a mock):

```json
{
  "company_overview": "Vapi is a unified platform that enables enterprises to build, test, and deploy voice agents quickly, ensuring fast, high-quality support for customers. The platform supports rapid production, 100% inbound volume routing, and improved CSAT scores.",
  "target_audience": "Enterprises and Fortune 100 companies requiring scalable, secure, and compliant voice agent solutions.",
  "contact_points": [],
  "leadership": [
    {
      "name": "Jason Mitura",
      "title": "VP of Software Development",
      "linkedin_url": "https://linkedin.com/company/vapi-ai"
    },
    {
      "name": "Jordan D.",
      "title": "",
      "linkedin_url": "https://www.linkedin.com/in/jordandearsley"
    },
    {
      "name": "Nikhil Gupta",
      "title": "",
      "linkedin_url": "https://www.linkedin.com/in/nikhilro"
    }
  ],
  "confidence_score": 0.64,
  "errors": [
    "company-page URL attributed to a person: Jason Mitura",
    "search_fallback: appended=2 backfilled=0"
  ],
  "domain": "vapi.ai",
  "leadership_sources": {
    "https://linkedin.com/company/vapi-ai": "regex",
    "https://www.linkedin.com/in/jordandearsley": "search_fallback",
    "https://www.linkedin.com/in/nikhilro": "search_fallback"
  }
}
```

- `leadership_sources`: core `/company/` URL tagged `regex` (deterministic
  page-evidence rule), both appended profiles tagged `search_fallback`.
- `confidence_score`: 0.64 (recomputed with `C = max(...)` = 0.25 evidence tier).
- `errors`: core note preserved plus `search_fallback: appended=2 backfilled=0`.

## 5. Git state

```
45988e4 chore: ignore front1.html local-only reference doc
dbea4f5 docs: bonus plan, LangChain NOT-BUILT seam and README note
89b979a feat: Tavily search fallback with LinkedIn recall and hermetic kill-switch
c5c9606 fix: email-only contacts, name-plausibility check, domain threading, hardened sample
dc12354 chore: untrack LOOM_SCRIPT and front.html (local-only reference docs)
```

Push state at verification time: `bonus` rev `45988e4` == `origin/bonus` rev
`45988e4` (in sync). `main` HEAD: `c5c9606` — unchanged before, during, and
after all verification runs. `output/output.json` was restored to its committed
state after record capture, so the only uncommitted items are this file (the
mandated deliverable), pre-existing local artifacts (`.omo/`, `output/` logs),
and nothing else.

## 6. Open item (reported, not fixed)

`test_degraded_math` fails if and only if a real Tavily key is present AND
`TAVILY_ENABLED=1`: live enrichment legitimately overwrites the test's faked
confidences. Hermetic modes (`TAVILY_ENABLED=0` or keyless) are fully green.
No action taken per the read-only instruction.
