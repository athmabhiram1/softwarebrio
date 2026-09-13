# softwarebrio-lead-agent - Work Plan

## TL;DR (For humans)

What you'll get: a production-grade `lead-enrichment-agent` Python pipeline that takes `postman.com, supabase.com, vapi.ai`, fetches homepage + ≤5 subpages per domain (httpx-first, Playwright only on thin cleaned text or 403/429/5xx), cleans with trafilatura→BS4 under a 6000-token cap (plus raw-HTML regex pre-pass for LinkedIn URLs/emails as extractor hints), runs ONE Groq (default `openai/gpt-oss-20b`) / Ollama-fallback LLM call per domain with Pydantic validation + single retry, and emits `output.json` (`{"records": [...], "summary": {...}}`) plus live per-domain terminal cost lines — all runnable via `docker compose up`.

Why this approach: it implements your locked v3 spec verbatim (no substituted architecture), with exactly two approved additions: deterministic confidence computed in `extractor.py` (LLM never self-grades) and live `raw KB → clean KB → tokens → $` terminal prints. Both are changes to existing pieces, not new modules, and both are directly demoable in the 2–3 min Loom.

What it will NOT do: no LangGraph/Browser-Use/agentic frameworks, no Tavily/LinkedIn founder search, no LLM self-rated confidence, no recursive crawling, no external-link following — seams only, marked NOT-BUILT, so the bonus phase plugs in later with zero rework.

Effort: ~10 implementation todos + 4 final verifiers; 48–72h intern timeline; local Python 3.11, Docker base `mcr.microsoft.com/playwright/python:v1.62.0-noble` pinned.

Risk: vapi.ai subpages are thinnest/hydrated (live evidence: 3.2k homepage chars vs 4.5k/8.7k) → most Playwright escalations there; Groq `llama-3.1-8b-instant` does not reliably support strict `json_schema` → default is `openai/gpt-oss-20b`; Docker image runtime is Python 3.12 → code stays 3.11-compatible and README notes it honestly.

Decisions: Groq-default (`openai/gpt-oss-20b`, verified strict json_schema) → Ollama-fallback free chain (MODEL_PROVIDER env switch; `http://host.docker.internal:11434/v1` in compose, localhost on host); Python 3.11 local; core + bonus-ready seams (Tavily micro-agent deferred, verified ~35 LOC / 1 credit per search when built); tests-after; output.json object + summary; ops answer YES.

## Scope

IN:
- `config.py` (env vars, model names, 10s timeout, post-clean 500-char threshold, 6000-token cap, rate-limit backoff, regex — no magic numbers elsewhere)
- `models.py` (Pydantic v2 TWO-MODEL split: `ExtractionPayload` for the LLM — all fields required, `additionalProperties: false`, NO confidence_score/errors — plus `CompanyRecord` final output adding computed confidence_score + `errors: list[str]` + Python-set `domain` (never LLM-emitted); leadership[name/title/linkedin_url], contact_points as regex-validated `list[str]`)
- `cleaner.py` (REGEX PRE-PASS on raw HTML per page BEFORE trafilatura: LinkedIn `linkedin\.com/(in|company)/[\w-]+`, emails via `mailto:` hrefs + generic pattern; dedupe per domain → `found_linkedin_urls`, `found_emails` passed as extractor-prompt hints the LLM maps to names/contact_points instead of inventing; then trafilatura `extract(output_format="txt", include_comments=False, include_tables=False, favor_precision=True)` → recall retry → BS4 `decompose/get_text` fallback; concat capped to 6000 tokens by page priority — trim/drop lowest first, highest first: team/leadership > contact > about > company > homepage > pricing (fix 3); raw-vs-cleaned log)
- `fetcher.py` (httpx AsyncClient + browser UA first; Playwright single-browser reuse escalated AFTER `clean_page` (imported from cleaner.py) when cleaned text is thin (<~500 chars) or status is 403/429/5xx — never on raw HTML size alone since JS shells are large but empty; `page.goto(url, wait_until="domcontentloaded", timeout=10000)`, never `networkidle`; per-URL method + clean-len log; 10s timeout; homepage + ≤5 regex subpages, depth-1, same-origin only)
- `extractor.py` (ONE OpenAI-compatible call/domain via Groq default / Ollama fallback — BOTH through the `openai` SDK client pointed at the respective base_url, NO `ollama` package per A6; `response_format json_schema` from `ExtractionPayload.model_json_schema()`; prompt receives `found_linkedin_urls`/`found_emails` regex hints; 429s retried up to 2x with exponential backoff (`RATE_LIMIT_BACKOFF_S`-based doubling: 5s, 10s for the default base 5; total sleeps always well under the ~45s cap) SEPARATE from the single ValidationError retry (a 429 retry never consumes the schema retry); then partial-record path; contacts reconciled regex-first AND filtered to email-pattern only (reserved RFC-2606 test domains, escape/entity remnants, phones, URLs, descriptive text dropped; raw HTML entity/unicode-decoded before the pre-pass; F recomputed post-filter); deterministic name-plausibility check (each leadership name must appear verbatim case-insensitively in that domain's cleaned text — missing names append `name not found in source text` errors + proportional penalty `0.20*(invented/total)`, floor 0.05); empty cleaned text skips the LLM entirely (0.05 partial + fetch errors, zero cost); domains_failed counts zero-populated records, not just exceptions; token/cost log; DETERMINISTIC confidence `0.45*F+0.25*P+0.20*C+0.10*Q` computed in Python with C=regex-provenance — LLM never self-grades; failures → 0.05 + errors)
- `main.py` (CLI `--domains`, batch isolation so one domain never stops others, LIVE per-domain terminal print + `output.json` as `{"records": [...], "summary": {total tokens/cost/runtime/failed}}` object — never a bare list)
- Dockerfile (SINGLE-STAGE ONLY, Fix 1 — no builder stage: FROM pinned `mcr.microsoft.com/playwright/python:v1.62.0-noble`, `pip install --no-cache-dir -r requirements.txt`, non-root `USER pwuser`, `init: true`, writable `/app/output`) + `docker-compose.yml` (`env_file: [.env]`, `extra_hosts: ["host.docker.internal:host-gateway"]` per Fix 4, `docker compose up` runs 3 domains) + `.dockerignore` (excludes `.env`)
- `requirements.txt` pinned `==`, `.env.example`, `README.md`, `output.json` from real run, Loom script outline, ops YES answer + submission email format
- Bonus-ready SEAMS only (NOT-BUILT): `search_fallback()` stub interface + cost-hook extension point, never called in core

OUT (Must-NOT-Have):
- No LangGraph, Browser-Use, or any agentic framework
- No Tavily/SerpAPI/LinkedIn lookup (deferred bonus phase)
- No LLM self-rated confidence (replaced by deterministic formula)
- No recursive crawling beyond depth 1, no external links, no `>=` requirements, no secrets baked in image, no bare `except: pass`, no per-page LLM calls, no more-than-once validation retry

## Verification strategy

- Tests-after pytest (run after each module, before moving on): schema validation, cleaner stripping/truncation, fetcher escalation heuristic (unit with fixtures), extractor confidence formula (pure-function cases), resilience (one-domain-fail isolation)
- Manual checks per task with exact invocations: fetcher escalation asserted on saved HTML fixtures (thin-cleaned fixture → Playwright, rich fixture → httpx-only); live runs (e.g. vapi.ai subpages) recorded as observational evidence only since live sites change
- Full Docker proof: `docker compose up --build` against the 3 live domains → `output.json` validates against Pydantic schema, summary totals reconcile, terminal shows 3 live cost lines
- Evidence paths: `output.json`, terminal log, `pip freeze` / image tag, pytest output

## Execution strategy

- Build order (dependency order): config → models → cleaner → fetcher (imports cleaner's clean_page) → extractor → main → docker/compose → README/env → live run → Loom/submission. One module per wave; no parallel module work (each imports the last).
- STOP-AND-SHOW DISCIPLINE (mandatory): complete exactly ONE todo per response; after each, stop and show the full QA evidence (commands + literal outputs) and wait for the user's go-ahead before starting the next todo — never batch todos. Todo 5's gate additionally requires showing the exact 400 + schema patch BEFORE any retry.
- Worker runs in the repo root `C:\Users\athma\OneDrive\Desktop\my projects\softwarebrio`; greenfield (no existing code to preserve).
- Stop rule per task: unit/manual check named in the todo must pass with evidence before the next todo starts.

## Todos

- [x] 1. config.py — env-driven constants (no magic numbers)
- [x] 2. models.py — Pydantic v2 strict schema + partial-failure shape
- [x] 3. cleaner.py — regex pre-pass + trafilatura→BS4 + 6000-token cap (priority trim) + size logs
- [x] 4. fetcher.py — httpx-first + conditional single-browser Playwright (imports cleaner)
- [x] 5. extractor.py — 1 call/domain, Groq-default/Ollama-fallback, 1 retry, deterministic confidence
- [x] 6. main.py — CLI batch orchestration + live cost print + output.json
- [x] 7. Dockerfile + compose + dockerignore (single-stage pinned, non-root, env_file, host-aware Ollama URL)
- [x] 8. requirements.txt pinned + .env.example + README + .gitignore
- [x] 9. Live Docker run vs 3 domains → output.json + terminal evidence
- [x] 10. Loom script outline + submission pack (ops YES + email format)

### 1. config.py
References: repo root (new file `config.py`); locked spec (10s timeout, post-clean ~500 chars, ~6000 tokens, regex `/about|team|company|contact|pricing|leadership`); research: httpx `Timeout(10.0)`, dotenv `load_dotenv()`.
Acceptance: `config.py` exposes `MODEL_PROVIDER, GROQ_MODEL (default "openai/gpt-oss-20b"), OLLAMA_MODEL, OLLAMA_BASE_URL (default "http://localhost:11434/v1" on host; compose overrides to `http://host.docker.internal:11434/v1` per Fix 4), FETCH_TIMEOUT_S=10, ESCALATE_MIN_CLEAN_CHARS=500 (post-clean, per P0b), TOKEN_BUDGET=6000 (A2), RATE_LIMIT_BACKOFF_S (base seconds for 429 backoff 5s/15s schedule, A2), LLM_FIELD_COUNT=4 (formula F divisor, fix 1), PAGE_PRIORITY (highest-first: team/leadership, contact, about, company, homepage, pricing — fix 3), SUBPAGE_RE, MAX_SUBPAGES=5`, all read from env with sane defaults; no other module hardcodes these values; `python -c "import config"` succeeds.
QA (happy): set `MODEL_PROVIDER=groq` in env → import shows groq default. QA (failure): unset env → defaults still load, no crash. Evidence: `python -c` output. Commit: `feat: add env-driven config`.

### 2. models.py
References: `models.py` (new); Pydantic v2 `model_json_schema / model_validate_json / model_dump / ValidationError.errors()`.
Acceptance: TWO-MODEL split — `LeadershipEntry{name, title, linkedin_url}` + `ExtractionPayload` (LLM-ONLY: company_overview (2-sentence str), target_audience (str), contact_points (`list[str]`, regex-reconciled — NO `EmailStr|str` union anywhere), leadership (`list[LeadershipEntry]`); every field required, `additionalProperties: false` at EVERY nesting level since `ExtractionPayload.model_json_schema()` is exactly what goes to Groq strict mode) + `CompanyRecord` (all payload fields PLUS Python-computed `confidence_score` (float 0.0–1.0) PLUS `errors: list[str]` default `[]` — the LLM never emits these two) + `RunSummary(total_tokens, total_cost_usd, total_runtime_s, domains_failed)`; `model_dump()` round-trips both; `CompanyRecord(confidence_score=9)` raises ValidationError; `ExtractionPayload` rejects a `confidence_score` key (additionalProperties false).
QA (happy): `pytest tests/test_models.py -q` — valid payload + record dump, `model_json_schema()` shows `additionalProperties: false` + full `required` at every level (feed this output to the Todo 5 gate). QA (failure): `confidence_score: 9` on CompanyRecord → ValidationError; any `confidence_score`/`errors` key on ExtractionPayload → ValidationError. Evidence: pytest output. Commit: `feat: add Pydantic two-model schema`.

### 3. cleaner.py
References: `cleaner.py` (new, imports `config.py` only — no fetcher dependency); trafilatura `extract(..., output_format="txt", include_comments=False, include_tables=False, favor_precision=True)` then `favor_recall=True` retry; BS4 `decompose()` on `script/style/svg/nav/footer/header` + `get_text(separator="\n", strip=True)`.
Acceptance: `clean_page(html)->{text, path: trafilatura|trafilatura-recall|bs4, raw_len, clean_len}`; `regex_prepass(raw_html)->{linkedin_urls, emails}` per page (A3/A7 patterns), deduped per domain in `concat_domain` which returns `{text, found_linkedin_urls, found_emails}` capped at ~6000 tokens by page priority — trim/drop lowest-priority pages first, highest first: team/leadership > contact > about > company > homepage > pricing (fix 3, NOT longest-first); per-domain log `raw KB → clean KB (~tokens)`.
QA (happy): feed saved postman HTML fixture → path `trafilatura`, clean_len < raw_len. QA (failure): feed empty/JS-shell HTML → falls to `bs4`, still returns string (possibly short) + fallback flag, never None-crash. Evidence: pytest + log lines. Commit: `feat: add cleaner`.

### 4. fetcher.py
References: `fetcher.py` (new, imports `config.py` AND `clean_page` from `cleaner.py` — built after cleaner per fix 2); httpx `AsyncClient(headers={UA}, timeout=10.0)` + `TimeoutException/HTTPStatusError` handling; Playwright `launch→new_context→new_page`, `page.goto(url, wait_until="domcontentloaded", timeout=10000)` — never `networkidle` (avoids hangs on background polling); `context.close()` before `browser.close()`; single shared browser across all domains.
Acceptance: `fetch_domain(domain)` returns per-URL `{url, method: httpx|playwright, status, html}`; homepage + ≤5 same-origin regex subpages, depth-1; 10s per page; per-URL method logged; one `launch()` per run (assert via log counter).
QA (happy): `python -m fetcher --domain postman.com` → homepage method `httpx` on fixture. QA (escalation): thin-cleaned JS-shell HTML fixture → method log shows `playwright` (asserted on fixtures only — live vapi.ai escalation recorded as observational evidence since live sites change). QA (failure): point at `http://127.0.0.1:9` or bogus domain → timeout/connection error caught, logged with exc type, returns partial not crash. Evidence: method-log lines. Commit: `feat: add httpx-first fetcher`.

### 5. extractor.py
References: `extractor.py` (new, imports `config.py`, `models.py`); Groq + Ollama BOTH via the `openai` SDK OpenAI-compat client (`OpenAI(base_url=..., api_key=...)`) — NO `ollama` package anywhere per A6; Groq `base_url="https://api.groq.com/openai/v1"`, `response_format json_schema(strict, ExtractionPayload.model_json_schema())`; usage `prompt_tokens/completion_tokens`, cost computed client-side.
GATE — Groq strict smoke test (Fix 3 + A4, MUST pass before Todo 5 is marked done; needs YOUR `GROQ_API_KEY`): send `ExtractionPayload.model_json_schema()` (nested `list[LeadershipEntry]`) as `response_format: {"type":"json_schema","json_schema":{"name":"ExtractionPayload","strict":true,"schema":<emitted>}}` to `openai/gpt-oss-20b` on one short fixture text; record the literal API status/body. If 400/`failed_generation` cites `additionalProperties`/`required` at any nesting level, patch the emitter (walk every object dict: set `additionalProperties:false` + complete `required`) and re-run to 200 — show the exact 400 + patch BEFORE retrying, never silent-loop. In the SAME gate call, also send `reasoning_effort="low"`: if 200, keep it as a `config.py` default (added then, not before); if rejected, drop it and add one README line explaining it was tried on <date> and removed because <literal error>. Paste status code + today's date into Todo 5 evidence AND into the README (A8) — "verified," never assumed.
Acceptance: `extract_domain(domain, cleaned_text)` makes EXACTLY 1 LLM call (2nd only on ValidationError, feeding `e.errors()` back once, then partial); LLM output never contains confidence (prompt forbids it); confidence computed as `round(min(1.0,max(0.0, 0.45*F+0.25*P+0.20*C+0.10*Q)),2)` with F=populated/4 (fix 1 — 4 LLM fields: overview, audience, contacts, leadership), P=pages_ok/attempted, C=1.0 regex /0.5 inferred/0.0 none, Q=1.0 clean /0.5 one-degradation/0.0 both; failures → 0.05 + errors.
QA (happy): mocked LLM JSON valid → record validates, confidence matches hand-computed value. QA (failure): mocked invalid JSON → 1 retry then partial + errors logged, call count == 2 max. QA (formula): unit cases (all-perfect→1.0; all-failed→0.05). Evidence: `pytest tests/test_extractor.py -q`. Commit: `feat: add extractor with deterministic confidence`.

### 6. main.py
References: `main.py` (new, imports config/models/fetcher/cleaner/extractor); locked output + live-print format `[{i}/{n}] {domain}: {mix} | raw {r}KB → clean {c}KB (~{t} tok) | LLM {in}/{out} tok ${cost} | conf {s} | {sec}s`.
Acceptance: `python main.py --domains postman.com supabase.com vapi.ai` processes domains SEQUENTIALLY in input order (Fix 5 — no `asyncio.gather` across domains; httpx overlap WITHIN one domain's pages only, shared single Playwright browser; rationale: deterministic `[{i}/{n}]` log order + flake-free Loom, accepted ~3x wall-clock cost); prints 3 live lines in input order; writes `output.json` as `{"records": [...], "summary": {...}}` (totals reconcile with per-domain logs); exit code 0 on partial failure.
QA (happy): run with 1 bogus + 2 real domains → 3 records, failed one has confidence ~0.05 + errors, summary `domains_failed==1`. QA (failure): no `--domains` → argparse error, no traceback dump. Evidence: terminal log + `output.json`. Commit: `feat: add orchestration and output`.

### 7. Dockerfile + compose
References: `Dockerfile`, `docker-compose.yml`, `.dockerignore` (new); SINGLE-STAGE ONLY — `FROM mcr.microsoft.com/playwright/python:v1.62.0-noble`, `pip install --no-cache-dir -r requirements.txt`, NO `COPY --from`, `USER pwuser`, `init: true`, writable `/app/output` mount; `playwright==1.62.0` in requirements must equal image's; compose `env_file: [.env]` + `extra_hosts: ["host.docker.internal:host-gateway"]` (Linux Engine workaround, no-op on Desktop).
Acceptance: `docker build -t lead-agent .` succeeds; `docker run --rm lead-agent python --version` noted; image runs as non-root (`whoami` → pwuser/appuser); `.env` NOT in image (`docker run --rm lead-agent ls -la` shows no `.env`); `docker compose up` runs the 3-domain command out of the box.
QA (happy): compose up finishes with `output.json` mounted out. QA (failure): build with missing requirements pin → fails fast with clear message. Evidence: build log + `docker images` tag. Commit: `chore: add Docker packaging`.

### 8. requirements + env + docs
References: `requirements.txt` (LOCKED FINAL — all `==`, no `>=`: httpx==0.28.1, playwright==1.62.0, trafilatura==1.10.0 [FROZEN: 2.x is a rewrite, researched API is 1.x], beautifulsoup4==4.15.0, lxml==5.3.0 [FROZEN: 6.x packaging breaks], pydantic==2.13.5, openai==1.51.0 [FROZEN: v3 major unverified with compat clients], python-dotenv==1.2.3, pytest==9.1.1, tiktoken==0.14.0. NO `groq` package (dropped as dead weight: both providers route through the `openai` SDK compat client, nothing imports it) and NO `ollama` package per A6. A1 (bumps to trafilatura/openai/lxml) CONSIDERED AND REJECTED — freeze stands.) `.env.example` (GROQ_API_KEY, MODEL_PROVIDER, models, timeouts); `README.md`; `.gitignore` (DECIDED Fix 2: ignores `.env`, `output.local.json`, `output-*.json`; tracks `output.json` exactly once as the committed real-run sample — reruns default to `output.local.json` unless `--out output.json` is passed).
Acceptance: fresh `python -m venv && pip install -r requirements.txt && playwright install` per README reproduces run; `.env.example` lists every var `config.py` reads; README covers setup, `docker compose up`, output shape, formula in one paragraph, limitations section.
QA (happy): follow README on clean checkout → run succeeds. QA (failure): no `.env` → runs on defaults/Ollama path with clear warning, no secret leak. Evidence: README + install log. Commit: `docs: add README and env template`.

### 9. Live run evidence
References: `output.json` (generated), terminal log, `Dockerfile`, `main.py`.
Acceptance: `output.json` contains 3 records matching schema + summary; per-domain token/cost lines present in both terminal and JSON; method log shows httpx-dominant with Playwright escalation (expected on vapi.ai subpages); total runtime recorded.
QA (happy): `python -m json.tool output.json` parses; Pydantic re-validate passes. QA (failure): kill network mid-run (or 1 bad domain) → still 3 records + `domains_failed` correct. Evidence: `output.json` + terminal transcript. Commit: `data: add sample output for 3 domains`.

### 10. Loom + submission
References: Loom script (new `LOOM_SCRIPT.md` or README section); assignment Sec 5/7 (GitHub link, requirements, README, output, 2–3min video, ops answer, subject `[AI Intern Submission] - [Name]`, LinkedIn).
Acceptance: script ≤3 min covering code map (30s), terminal run with live cost lines (60s), `output.json` + confidence explanation using the Loom sentence (45s), limitations + ops YES (15s); ops answer line present verbatim; email subject line + LinkedIn profile line documented (assignment §7 requires LinkedIn in the email); README carries the Todo 5 smoke-test provenance line verbatim (A8): literal Groq HTTP status + date (e.g. "Groq strict json_schema smoke test: HTTP 200 on 2026-09-12; reasoning_effort=low <kept|dropped: reason>").
QA: dry-read script aloud ≤180s. Evidence: `LOOM_SCRIPT.md`. Commit: `docs: add Loom script and submission checklist`.

## Final verification wave

- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Scope fidelity

F1: every locked-spec bullet exists in code (httpx-first, post-clean thin-text/403/429/5xx escalation, single browser, 10s, regex depth-1, trafilatura→BS4, 6000 cap, 1 call/domain, env model, 1 retry, per-call try/except, partial+errors, output.json object+summary, pinned base, single-stage (Fix 1), non-root, env secrets, compose env_file + extra_hosts (Fix 4), 6 modules, type hints, ≤40-line funcs, locked == reqs per Todo 8 (no groq/ollama packages)) + the 2 folded changes (formula + live print) with no substitutions. F2: type hints throughout, no func >40 lines, no bare except, no hardcoded model/timeouts. F3: fresh-clone → pip install → run + `docker compose up` vs 3 domains → output.json validates. F4: no LangGraph/Browser-Use/Tavily/LinkedIn/LLM-self-confidence/recursive-crawl present; seams marked NOT-BUILT only.

## Commit strategy

- One commit per todo (10 commits, prefixes above); final verifiers uncommitted checks. No squashing before review so the reviewer sees modular history.

## Success criteria

- `docker compose up --build` completes against postman.com, supabase.com, vapi.ai with exit 0 and produces schema-valid `output.json` + summary + 3 live terminal cost lines.
- Confidence for every record is formula-computed (auditable by hand) and explainable in one Loom sentence; no LLM-graded confidence anywhere.
- README + `.env.example` + pinned requirements let a stranger reproduce the run; Loom script ≤3 min; ops YES answer and email subject present.
- Deliberate non-goals documented in README known-limitations (below), each with its one-sentence why.

### Known limitations (for README — deliberate, not oversights)
- No LLM self-rated confidence — deterministic formula instead, because small chat models are miscalibrated and fluency is not evidence.
- No LinkedIn/Tavily founder search — deferred to the bonus phase after core runs Docker-green, since homepage evidence shows leadership names are customer quotes that need external verification to avoid misattribution.
- No LangGraph/Browser-Use/agentic framework — a fixed httpx-first + conditional-Playwright pipeline is deterministic, cheaper, and demoable in 2 minutes where an agent loop would add flake.
- No strict Python 3.11 runtime inside Docker — base image `v1.62.0-noble` ships Python 3.12, so code is written 3.11-compatible and the version is noted honestly rather than faking a pin.
- Homepage-only server render assumed for happy path — vapi.ai-style hydrated subpages escalate to Playwright by design and are logged as such instead of pretending plain HTTP always suffices.
