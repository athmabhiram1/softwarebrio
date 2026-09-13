# PROJECT_EXPLAINED.md

Read-first notes for lead-enrichment-agent. Every claim below traces to code or docs in this repo. Where the reason isn't in the code, that's stated plainly.

## 1. What this project does

You give it company domains (default `postman.com,supabase.com,vapi.ai`), and it crawls each homepage plus up to 5 same-origin subpages, cleans the HTML to token-capped text, and makes one LLM call per domain to pull out a 2-sentence overview, target audience, contact emails, and leadership with LinkedIn URLs. Each record gets a Python-computed confidence score, and a Tavily search step patches in founders when the core crawl only yields company-page links. Current `output/output.json` holds 3 records scoring 0.95, 0.64, 0.64 with `tavily_calls: 2, tavily_credits: 2` and `domains_failed: 0`.

## 2. Architecture overview

Text flow:

```
domains -> fetch (httpx, Playwright only on escalation) -> clean (regex pre-pass + trafilatura/BS4, 6000-token cap) -> extract (one Groq strict-schema LLM call) -> score (Python confidence + name check) -> [fallback: Tavily LinkedIn search + append + rescore, only when triggered] -> output/output.json {records, summary}
```

A fixed pipeline was picked over an agent framework for reasons stated in the docs, not assumed here. README's Known limitations says a fixed httpx-first plus conditional-Playwright pipeline "is deterministic, cheaper, and demoable in 2 minutes where an agent loop would add flake," and docs/LANGCHAIN_RESEARCH.md's verdict (section 5) calls an agent loop "not-worth-building," citing unbounded-run risk, about 15 to 20 transitive deps, a forced `openai==1.51.0` pin break, and zero scoring upside since the graded work (cost tracking plus live Tavily fallback) already ships.

## 3. Per-module walkthrough

### config.py: single source of every tunable

It reads all settings from env with sane defaults, and its docstring forbids other modules from hardcoding model names, timeouts, thresholds, or regexes. Key picks: `MODEL_PROVIDER` defaults to `groq` with `GROQ_MODEL=openai/gpt-oss-20b`, both LLM paths go through the `openai` SDK OpenAI-compat client (no `groq` or `ollama` package), and `TAVILY_ENABLED` defaults true so production calls Tavily unless tests set `TAVILY_ENABLED=0`. Cold-reader note: Tavily knobs (`TAVILY_QUERY_TEMPLATE`, `TAVILY_INCLUDE_DOMAINS=linkedin.com`, depth `basic`, max 5) lived here before the fallback was built, so the seam needed no rework later.

### models.py: two-model split between LLM and output

`ExtractionPayload` is the only shape the LLM may emit (all fields required, `additionalProperties: false` at every level, fed verbatim to Groq strict `json_schema`). `CompanyRecord` is final output, meaning payload fields plus `confidence_score` and `errors`, which the LLM never emits. Decisions that matter: `LeadershipEntry.linkedin_url` has no default so strict mode forces the key (empty string when unknown), and `leadership_sources` defaults to `{}` so core-only records carry an empty map until fallback tags it. One subtlety: `CompanyRecord.from_payload()` copies payload fields by `model_dump()`, so later filtering mutates the payload copy held by the record.

### cleaner.py: evidence first, readable text second

`regex_prepass()` scans raw HTML for `linkedin.com/in` or `/company/` URLs and emails (mailto first, then bare pattern, deduped, lowercased) before any cleaning, which gives the C signal independent provenance. `clean_page()` tries trafilatura precision, then trafilatura recall, then BS4 with script/style/nav/footer/header/aside/form/noscript/iframe stripped plus comments removed, and it records which `path` won. `concat_domain()` merges per-page text then shrinks to `TOKEN_BUDGET=6000` (tiktoken `cl100k_base`, fallback `len//4`) by cutting the lowest-priority page 20 percent at a time, with priority from `PAGE_PRIORITY` (`team,leadership,contact,about,company,homepage,pricing`). Missable behavior: homepage detection treats a bare domain as homepage, trimming skips `homepage` when matching tokens, and pages cut below 100 chars of stripped text are emptied outright.

### fetcher.py: httpx first, Playwright only on cause

`fetch_page_httpx()` is the default with browser user agent, 10s timeout, and redirect following. `needs_playwright()` returns true on 403, 429, 5xx, request timeout (`status is None`), or cleaned text thinner than `ESCALATE_MIN_CLEAN_CHARS=500`, but never on 404 and never on raw HTML size. `discover_subpages()` keeps only same-netloc hrefs whose path matches `SUBPAGE_RE` (`about|team|company|contact|pricing|leadership`), strips fragments, and caps at `MAX_SUBPAGES=5`. A single shared browser is reused (`ensure_browser`/`close_browser`), and `_fetch_one()` keeps httpx HTML as fallback when Playwright errors. Unclear from code alone, worth confirming: exact live per-domain httpx vs Playwright mix for the current `output.json` run isn't persisted in the JSON, only the derived `degraded` math downstream reflects it.

### extractor.py: one LLM call, all judgment in Python

`build_messages()` sends cleaned text plus `REGEX_HINTS` (found LinkedIn URLs and emails) with a system prompt telling the model to map hints instead of inventing, and `call_once()` uses `temperature=0` with strict `json_schema` named `ExtractionPayload`. Retry policy splits concerns: `_call_with_429()` retries up to 3 attempts with `RATE_LIMIT_BACKOFF_S * 2^attempt` sleeps, while `extract_with_retry()` allows exactly one schema-validation retry that appends the raw output plus validator errors. Contact hygiene (`_keep_contact`) drops non-emails, unicode-escaped or entity-encoded strings, and `example.*` or `test`/`localhost`/`invalid` domains; `_check_names()` flags any leadership name missing as a case-insensitive substring of cleaned text. Failures collapse to `confidence_score=0.05` with `errors` explaining why, so one domain can't crash the batch.

### main.py: batch glue and bookkeeping

`run_batch()` loops domains sequentially, threads fetch, clean, extract, and optional fallback, then writes `{"records": [...], "summary": {...}}` to the `--out` path (default `output.local.json`, tracked sample is `output/output.json`). `_compute_degraded()` re-cleans each page to get its path and scores `degraded = (any path != trafilatura) + (any method == playwright)`, capped at 2. `_calc_pages_ok()` derives crawl success from fetch errors by URL substring match. Live log lines show mix, raw vs clean KB, token counts, cost, confidence, seconds, and Tavily calls/credits; empty cleaned text short-circuits to a 0.05 record without calling the LLM. Worth knowing: Tavily meta (`tavily_calls`, `tavily_credits`) merges into the LLM cost meta for the log line, while token/cost summary totals exclude Tavily spend.

### search_fallback.py: narrow patch for missing founders

It fires only via `should_trigger()` (see section 5), posts directly to Tavily with `httpx`, parses at most 3 `/in/` person profiles while skipping `/company/` URLs, and merges by normalized-name match (append new people, backfill empty URL/title on same names). `_PROV_VALUES` tags each leadership URL as `regex=1.0`, `llm_inferred=0.5`, or `search_fallback=0.25`, then `_recompute_confidence()` rescores with `compute_confidence(f_pop, 0, 0, contact_mode, 0)` where mode comes from the max tag. If Tavily finds nothing, the core record stands untouched (no rescore, `leadership_sources` stays `{}`), and every append adds a `search_fallback: appended=X backfilled=Y` error-line receipt.

## 4. Confidence formula ELI5

Think of it as four school grades blended by fixed weights. Field completeness (F) asks how many of 4 slots got filled (overview, audience, contacts, leadership). Crawl success (P) asks what share of fetched pages yielded usable text. Contact proof (C) asks how solid the contact evidence is. Pipeline cleanliness (Q) asks whether trafilatura and plain httpx sufficed. Code (`extractor.py:compute_confidence`) is:

```
round(min(1.0, max(0.0, 0.45*(f_pop/4) + 0.25*(pages_ok/pages_total) + 0.20*C + 0.10*Q)), 2)
```

C values: `regex=1.0`, `inferred=0.5`, `search_fallback=0.25`, `none=0.0`. Q values: `degraded 0=1.0`, `1=0.5`, else `0.0`.

Worked example from current `output/output.json` (supabase.com, score 0.64): after fallback the record shows overview present, audience present, `contact_points: []`, leadership present (2 entries), `leadership_sources` with one `regex` and one `search_fallback` tag, and errors `["company-page URL attributed to a person: Jakob Steinn", "search_fallback: appended=1 backfilled=0"]`. Rescoring (`search_fallback.py:_recompute_confidence`) always calls `compute_confidence(f_pop=3, pages_ok=0, pages_total=0, contact_mode, degraded=0)`, and mode comes from max tag `1.0`, so mode is `regex`. Plug real numbers:

```
F: 3/4 = 0.75, 0.45 * 0.75 = 0.3375
P: 0/0 -> 0, 0.25 * 0 = 0.0
C: regex = 1.0, 0.20 * 1.0 = 0.20
Q: degraded 0 = 1.0, 0.10 * 1.0 = 0.10
sum = 0.3375 + 0.0 + 0.20 + 0.10 = 0.6375, round -> 0.64
```

That 0.64 matches the stored `confidence_score` exactly. vapi.ai in the same file works identically (overview plus audience plus leadership, no contacts, max tag `regex` from its surviving `/company/` URL, `0.3375 + 0 + 0.20 + 0.10 = 0.6375 -> 0.64`).

Name-verification penalty is separate and didn't fire in this sample. When any emitted leadership name is absent from cleaned text (case-insensitive substring, `extractor.py:_check_names`), code applies `name_penalty = 0.20 * (missing / total)` only if leadership is non-empty and at least one name is missing, then `round(max(0.05, confidence - penalty), 2)`. Current JSON has no "possible LLM invention" errors, so no penalty was subtracted here. Tier logic is deliberate: `regex` (1.0) means the URL or email literally appeared in fetched HTML, `inferred` (0.5) means only the LLM claims it, and `search_fallback` (0.25) means it came from outside search results, which is weaker than page evidence and therefore scores below both.

## 5. Tavily fallback end-to-end

Trigger condition is narrow, quoted from `search_fallback.py:should_trigger`:

```
def should_trigger(rec: CompanyRecord) -> bool:
    if not rec.leadership:
        return True
    for le in rec.leadership:
        ...
        if url and "linkedin.com/company/" not in url:
            return False
    return True
```

Put simply, it fires when leadership is empty or every URL is a `/company/` page (or blank). postman.com has three `/in/` person URLs, so it didn't fire. supabase.com and vapi.ai each kept one `/company/` URL from core extraction, so both fired, matching `summary.tavily_calls: 2`.

Transport is a raw `httpx.post` to `TAVILY_URL = "https://api.tavily.com/search"` with `Authorization: Bearer <TAVILY_API_KEY>`, body `query` from `"{domain} founder OR CEO OR co-founder linkedin"`, `search_depth: basic`, `max_results: 5`, `include_domains: ["linkedin.com"]`. There's no SDK because `requirements.txt` pins exactly 10 packages (httpx, playwright, trafilatura, beautifulsoup4, lxml, pydantic, openai, python-dotenv, pytest, tiktoken) and README's honesty notes stress locked pins; a `tavily-python` dep would add a fresh conflict surface that docs/LANGCHAIN_RESEARCH.md section 4 already warns about for new deps.

Real before/after from current `output/output.json`: supabase.com post-fallback holds 2 leaders (`Jakob Steinn`, Co-founder and Tech Lead, `https://linkedin.com/company/supabase`; `Greg Kress`, title empty, `https://www.linkedin.com/in/gregorykress`), errors include both the company-page note and `search_fallback: appended=1 backfilled=0`, `leadership_sources` maps those two URLs to `regex` and `search_fallback`, score 0.64. Its core-only state (reconstructed by stripping the one appended `/in/` entry) was a single company-page leader with `leadership_sources: {}` and only the company-page error; the pre-enrichment score for this exact run isn't persisted, so the core number here is unclear from code alone, worth confirming, though docs/BONUS_VERIFICATION.md section 4 shows the same single-entry shape scoring 0.84 in its Docker run. vapi.ai post-fallback holds 3 leaders (`Jason Mitura` on `https://linkedin.com/company/vapi-ai`, `Jordan D.` on `https://www.linkedin.com/in/jordandearsley`, `Gerard James Roy` on `https://www.linkedin.com/in/gerardjamesroy`), errors include `search_fallback: appended=2 backfilled=0`, sources tag the company URL `regex` and both `/in/` URLs `search_fallback`, score 0.64.

Failure paths differ on purpose. On HTTP 429 (or a 429-carrying exception), `search_tavily` sleeps `RATE_LIMIT_BACKOFF_S * 2^attempt` and retries up to 3 attempts, since rate limits are often transient. On any other non-200 status, JSON parse failure, or transport error, it logs a warning and breaks immediately with `([], meta)` holding `tavily_calls: 0`, because retrying a malformed request or bad key won't help and the core record must stand as a safe no-op.

## 6. Known limitations restated from README plus LANGCHAIN verdict

README's six, each with its given reason: no LLM self-rated confidence, because small chat models are miscalibrated and fluency isn't evidence; no LinkedIn/Tavily founder search in core, deferred to bonus after Docker-green since homepage names looked like customer quotes needing external verification; no LangGraph/Browser-Use/agentic framework, because the fixed pipeline is deterministic, cheaper, and demoable in 2 minutes where a loop adds flake; no LangChain extraction/orchestration (NOT-BUILT seam docstring only), because single-call extraction is cheaper and more reliable for 3 fixed domains; no strict Python 3.11 inside Docker (image ships 3.12), noted honestly rather than faking a pin; homepage-only server render assumed for the happy path, since hydrated subpages escalate to Playwright by design. docs/LANGCHAIN_RESEARCH.md adds the verdict: agent layer not worth building post-submission, since it adds unbounded-run risk plus 15 to 20 transitive deps and forces an `openai==1.51.0` break for zero rubric credit, and should only be built if all of its four gates hold (post-submission branch, full 3-day-plus live plus clone plus Docker gate, byte-identical extractor with kill-switch, named consumer).

## 7. FAQ anticipating reviewer questions

### Why not LangChain?

Docs answer this twice. README says the deterministic single call is cheaper and more reliable for 3 fixed domains, and docs/LANGCHAIN_RESEARCH.md sections 4 to 5 add that `langchain==1.3.x` drags `langchain-core`, `langgraph`, `langsmith` plus checkpoint packages and breaks the frozen `openai==1.51.0` pin, with a realistic 3 to 5 day stabilization bill against no bonus rubric row.

### Is confidence made up by the model?

No. Prompts forbid emitting `confidence_score`, `models.py` keeps it off the LLM schema, and `extractor.py:compute_confidence` plus `search_fallback.py:_recompute_confidence` derive it from counts and provenance tags. Anyone can recompute supabase.com's 0.64 from section 4 with the JSON alone.

### What happens on a nonexistent domain?

Fetch errors accumulate per URL, cleaning yields little or nothing, and `main.py` short-circuits empty cleaned text to a 0.05 record with fetch errors preserved. Even if extraction is attempted and throws, the exception path builds the same 0.05 shape and `run_batch` counts it in `domains_failed` without stopping siblings.

### What if fallback finds nothing?

That's the designed safe no-op. docs/BONUS_VERIFICATION.md section 4 shows supabase.com firing (`Tavily 1/1c`) yet keeping its single company-page leader, empty `leadership_sources`, untouched core score, and no `search_fallback` receipt line when zero `/in/` candidates parse. Fallbacks also skip entirely without a key or when `TAVILY_ENABLED=0`, which is how the hermetic suite stays green (56 passed in 2.80s per that doc).

## 8. One-paragraph say-it-out-loud summary

This is a fixed fetch, clean, extract, score pipeline with one optional search patch, and you can say it in about thirty seconds. It crawls each domain's homepage plus matching subpages with fast httpx and only renders with Playwright when pages block or come back thin, cleans with trafilatura backed by BeautifulSoup under a 6000-token budget, extracts with a single strict-schema Groq call that can't grade itself, scores deterministically from filled fields plus crawl success plus regex proof plus path cleanliness with a penalty for names missing from source text, and only then fires a raw Tavily POST for LinkedIn founders when leadership is empty or company-page only, which is why postman.com sits at 0.95 untouched while supabase.com and vapi.ai each gained real `/in/` profiles and rescored to 0.64 with full receipts in errors and leadership sources.
