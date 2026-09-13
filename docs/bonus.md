# Bonus Branch Plan — Tavily Search Fallback (Task 1) + LangChain Seam (Task 2)

> Branch: `bonus` (from `main` @ `c5c9606`). Status: APPROVED with hardening fixes — Todo 1 in progress.

## 0. Locked decisions

| # | Decision | Locked value |
|---|---|---|
| 1 | Key handoff | `TAVILY_API_KEY` in `.env`, never committed. If unset/empty, fallback skips silently with `errors` note, core record unchanged |
| 2 | Budget | Cheap: `search_depth="basic"` (1 credit), `max_results=5` returned, max 3 appended, single query per domain, 10s httpx timeout |
| 3 | Trigger | Strict only: `leadership == []` OR every entry's `linkedin_url` fails person-vs-company check. Partial-valid records NOT supplemented (forced-trigger relaxation allowed in TEST only for dry run, never prod) |
| 4 | Confidence | `C={"regex":1.0,"inferred":0.5,"search_fallback":0.25,"none":0.0}`, mixed = max across entries, reuse `compute_confidence` directly |
| 5 | Cost tracking | Same `meta` + `summary` objects, keys `tavily_calls/tavily_credits/tavily_status/tavily_query` |
| 6 | Retry/log | 429 → reuse `extractor.py` exact backoff (3 attempts, `RATE_LIMIT_BACKOFF_S * 2^attempt`); all other failures → no retry, `logger.warning` with domain + type + status, fall through to unchanged core record |
| 7 | Dependency | No new dep. Raw `httpx.POST https://api.tavily.com/search`. `requirements.txt` stays 10 pins |
| 8 | Gate domain | `supabase.com` live gate + one forced-trigger dry run in TEST only |
| 9 | Seam | `NOT_BUILT_langchain_seam.py` at root + README entry, zero imports, never called. ReAct loop considered but not built (scope/risk) |

## 1. Codebase findings (conventions to match)

- `config.py:99` — `TAVILY_API_KEY` seam exists, never called by core. New tunables go here via `_get_int`/`os.getenv`.
- `models.py:18-31` — `LeadershipEntry` + `ExtractionPayload` are strict (`extra="forbid"`, all-required, `model_json_schema()` → Groq). `CompanyRecord:34-41` = payload + `confidence_score` + `errors` + `domain`. `RunSummary:59-63` = 4 keys. No `source` field today.
- `extractor.py:88-99,131-157` — `_is_429` + `_call_with_429`: 3 attempts, `sleep = RATE_LIMIT_BACKOFF_S * 2^attempt`. Only 429 retries.
- `extractor.py:198-214,240,243-252` — `compute_confidence` (`0.45*F+0.25*P+0.20*C+0.10*Q`), `contact_mode` derivation, `linkedin.com/company/` check, name-mismatch penalty, `0.05` floor.
- `cleaner.py:25` — page-evidence regex `linkedin\.com/(?:in|company)/[\w-]+`. Fallback accepts `/in/` only.
- `main.py:82-114,117-142` — `_process_single` is the only insertion point; `run_batch` aggregates `total_tokens/total_cost/domains_failed` into `{"records":[],"summary":{}}`.
- Style: type hints, ≤40-line functions, per-call try/except, no bare excepts, `logging.getLogger(__name__)`.

## 2. Tavily research (docs.tavily.com, verified)

- Endpoint `POST https://api.tavily.com/search`, auth `Authorization: Bearer tvly-…`, body `{"query":"<domain> founder OR CEO OR co-founder linkedin","search_depth":"basic","max_results":5,"include_answer":false}`.
- Cost: `basic` = 1 credit; `advanced`/`auto_parameters` = 2 — hence force `basic`, never send `auto_parameters`.
- Success 200: `{"query":str,"results":[{"title","url","content","score"}],…}`. We parse `results[].url/title/content` only.
- Errors: 400/401/429/432/433/500. Only 429 retries; 432/433 fail fast with distinct logs.
- Pin conflict: none (httpx already frozen at `0.28.1`).

## 3. Insertion architecture + trigger (HARDENED)

Process in `main.py::_process_single`, strictly after `extract_domain`, wrapped in try/except that never re-raises:

1. Fetch → clean → `extract_domain` (unchanged).
2. If `config.TAVILY_API_KEY` and `should_trigger(rec)`: `search_tavily` → `extract_linkedin_profiles` (cap 3, named only) → `enrich_record` (dedupe-by-normalized-name + backfill, else append), `meta.update()` same dict including `tavily_query`.
3. Existing live print + return (extended with Tavily segment, same line).

`should_trigger(rec)`: True iff `not rec.leadership` OR every `linkedin_url` empty or containing `linkedin.com/company/` (verbatim reuse of `extractor.py:244` predicate). `found_li` passed through for source tagging only. Forced-trigger relaxation lives in TEST only (monkeypatch/stub rec), never in `config.py` or prod.

## 4. Confidence + provenance, zero strict-schema touch (HARDENED)

- `ExtractionPayload`/Groq contract FROZEN. No new field, no prompt change.
- `search_fallback.py` imports and calls `extractor.compute_confidence` directly — no formula duplicate. Mixed-provenance rule: `C = max(provenance value across all leadership entries)` — best available evidence wins.
- Values: `{"regex":1.0,"inferred":0.5,"search_fallback":0.25,"none":0.0}`. `search_fallback` mode only when ≥1 named candidate appended/backfilled.
- `source` derived deterministically: core entries `regex` if URL in `found_linkedin_urls` set else `llm_inferred`; fallback entries `search_fallback`.
- Storage: `CompanyRecord.leadership_sources: dict[str,str]` (optional, default `{}`), keyed by `linkedin_url`. `LeadershipEntry` untouched; existing tests pass via default.

## 5. Cost/call tracking (same objects, HARDENED)

- `meta.update({"tavily_calls":0|1,"tavily_credits":0|1,"tavily_status":int|None,"tavily_query":str})` — literal query string sent, alongside status. No USD math for credits; `total_cost_usd` untouched.
- Live print adds `| Tavily {calls}/{credits}c {status}` in place. Summary adds `tavily_calls/tavily_credits` keys in place (`tavily_query` stays per-domain in meta/logs, not summed).

## 6. New module + config (HARDENED)

- `config.py`: `TAVILY_TIMEOUT_S`(10), `TAVILY_MAX_RESULTS`(5), `TAVILY_SEARCH_DEPTH`(`basic`), `TAVILY_QUERY_TEMPLATE`(`{domain} founder OR CEO OR co-founder linkedin`), `TAVILY_INCLUDE_DOMAINS`(`["linkedin.com"]`), `TAVILY_INCLUDE_DOMAINS_MODE`(`"filter"`). Mirror in `.env.example`.
- `search_fallback.py` (≈170 lines, seven ≤40-line funcs; imports `compute_confidence` from `extractor`):
  - `should_trigger(rec) -> bool`
  - `search_tavily(domain) -> tuple[list[dict], dict]` (body `{query, search_depth, max_results, include_answer:false, include_domains:["linkedin.com"], include_domains_mode:"filter"}` — domain scoping added after the first live gate returned 0 `/in/` unscoped; returns results + `{tavily_calls:1,tavily_credits:1,tavily_status,tavily_query}`)
  - `_normalize_name(n) -> str` (helper: casefold + strip punctuation/whitespace) + `parse_name_title(title, content) -> tuple[str,str] | None` (dash `Name - Title | LinkedIn` shape, plus bare display names e.g. `Grant Huston` → `(name, "")` with credential-strip and non-person stoplist; title optional, name required)
  - `extract_linkedin_profiles(results) -> list[tuple[url,name,title]]` (drop `/company/`, drop unparseable, cap 3)
  - `enrich_record(rec, candidates, found_li) -> tuple[CompanyRecord, dict]` (for each candidate: if `_normalize_name(candidate) == _normalize_name(existing)` → backfill that entry's `linkedin_url` + set `leadership_sources[url]="search_fallback"` instead of appending; else append named `LeadershipEntry`; backfill core `leadership_sources` via §4 rule; recompute via imported `compute_confidence` with `C = max(...)` passed through as `search_fallback` mode (not collapsed to `none`); `errors` note)
- Tests: `tests/test_search_fallback.py` only (mocked `httpx.post`); existing 4 files untouched. Cases: trigger, dedupe-backfill (no duplicate person), C-max + C-contribution (0.49), cap-3, unparseable-dropped, bare-display-name accept/guard, meta includes `tavily_query`, body sends domain scope.

## 7. Smoke-test GATE + forced-trigger dry run (HARDENED, blocks done)

1. Authorized single live call for `supabase.com`; paste literal status + body (same command as before, `max_results:5`, `basic`).
2. Then one forced-trigger dry run in TEST only: temporarily relax `should_trigger` via monkeypatch/stub on a domain that wouldn't naturally trigger, exercising `enrich_record` append/backfill/confidence-recompute end-to-end. Never touches `config.py` or prod code. Paste pytest output + resulting record fragment as evidence that enrichment ran, not just the API call.

Pass = (1) 200 + ≥1 `/in/` URL, and (2) dry-run test green showing append/backfill path executed.

Precision note (verified independently 2026-09-12): the domain-scoped re-gate on `supabase.com` surfaced Grant Huston — a real, current Supabase employee (Account Executive, per his own public LinkedIn activity) — plus one more real profile, both appended end-to-end with `source: search_fallback`. The fallback surfaces real team members via search; it does not guarantee founders/executives specifically, despite the query's title framing. Score it as verified-people recall, not founder recall.

## 8. Tests + regression

- `pytest -q` green (existing unmodified + new file incl. dedupe/C-max/cap-3/tavily_query/forced-trigger cases).
- Final: `docker compose up --build` fresh-clone regression, 3-domain run reconciles.

## 9. Task 2 seam (no-op, HARDENED)

`NOT_BUILT_langchain_seam.py` at root (docstring only, zero imports, never imported) + README §8 bullet in NOT-BUILT style. No `langchain` import, no dep, never called. Both files add one sentence: a custom tool-calling loop was considered (a linear 3-tool ReAct loop doesn't need a framework per current framework-comparison guidance) but not built, to keep this branch's scope and risk minimal given the submission timeline. Rejects both custom ReAct loop and real LangChain integration. Task 2 stays exactly as approved.

## 10. Wave order (approved, executing)

- Todo 1 (now): `tests/test_search_fallback.py` RED → `search_fallback.py` GREEN
- Then: config + `.env.example` → C-map + `leadership_sources` + hookup → GATE + dry run → seam + README → pytest + docker
