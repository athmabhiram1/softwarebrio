# lead-enrichment-agent

> Give it three company websites, and it returns a short profile for each: what the company does, who it's for, contact emails, and leaders with LinkedIn links.
> Output is one JSON file (`output.json`) with 3 records plus run totals.
> It works today: postman.com, supabase.com, and vapi.ai ran clean (0 failed) in 60.19s for 19,584 tokens at $0.001195, with confidences 0.95, 0.49, and 0.64.

## Run it in 5 minutes

Pick one path. Both use the same three domains and write a JSON file.

### Path A: Docker (easiest, one command)

```bat
copy .env.example .env
docker compose up --build
```

Edit `.env` first and set `GROQ_API_KEY` (get one free at console.groq.com). That's the only key the default path needs. Without a Tavily key the search fallback just skips silently, nothing breaks. Compose reads `.env` through `env_file`, runs `python main.py --domains postman.com,supabase.com,vapi.ai --out /app/output/output.json` inside the container, and writes `./output/output.json` on your machine (matches `docker-compose.yml`).

### Path B: Local (Python 3.11)

```bat
py -3.11 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
playwright install
copy .env.example .env
```

Then set `GROQ_API_KEY=` in `.env`, and run:

```bat
py -3.11 main.py --domains postman.com,supabase.com,vapi.ai --out output.local.json
```

macOS/Linux folks: swap `copy` for `cp .env.example .env` and `py -3.11` for `python3.11`. If you'd rather skip Groq, set `MODEL_PROVIDER=ollama` and point `OLLAMA_BASE_URL` at your local server (defaults to `http://localhost:11434/v1`). All other settings already have working defaults.

### Keys

- `GROQ_API_KEY`: required for the default Groq path.
- `TAVILY_API_KEY`: optional. Set it and weak leadership records get a LinkedIn search fallback. Leave it empty and that step skips silently (logged as Tavily 0/0c).

### What success looks like

You'll see 3 live lines, one per domain, shaped like this:

```
[{i}/{n}] {domain}: {mix} | raw {r}KB -> clean {c}KB (~{t} tok) | LLM {in}/{out} tok ${cost} | conf {s} | {sec}s | Tavily {n}/{nc}c
```

Then check the JSON: `output.json` (Docker) or `output/output.local-<ts>.json` (local run, stamped) holds `{"records": [...], "summary": {...}}`. Never a bare list. The two stable names (`output.json`, `output.local.json`) are stamped `name-YYYYMMDD-HHMMSS.json`; bare names with no directory are routed into `output/` (created if missing), so reruns never clobber the tracked `output.json` sample; any other `--out` path is written verbatim.

## What it does (plain language)

Type in company domains. The tool visits each site's homepage plus up to 5 matching subpages (`/about`, `/team`, `/company`, `/contact`, `/pricing`, `/leadership`), reads the pages like a researcher would, and asks an AI model once per domain for a fixed summary: 2-sentence overview, target audience, contact emails, and leaders (name, title, LinkedIn). Every domain gets a 0.0 to 1.0 confidence score, and one bad site can't crash the rest.

The checked-in sample covers postman.com, supabase.com, and vapi.ai. Real totals from `output/output.json`: 19,584 tokens, $0.001195 (LLM only), 60.19s runtime, 0 failed, plus `tavily_calls: 2, tavily_credits: 2`. Per-domain confidences are postman.com 0.95 (3 leaders, 3 emails), supabase.com 0.49 (1 leader via fallback, no emails), vapi.ai 0.64 (2 leaders, no emails).

## How it works

- Fetch: fast HTTP first (`httpx`), headless browser (`Playwright`) only when a page comes back too thin.
- Clean: strip menus, scripts, and raw HTML (`trafilatura`, then `BeautifulSoup`), cap at 6,000 tokens by page priority so the model sees the good parts.
- Extract: one structured call per domain (Groq `openai/gpt-oss-20b` by default, Ollama fallback) with a strict schema, so the JSON shape never drifts.
- Score: deterministic Python math rates each record, the model never grades itself.
- Fallback: if leadership is missing or company-page only, one Tavily LinkedIn search can append up to 3 `/in/` profiles.
- Output: records plus a `summary` with tokens, cost, runtime, failures, and Tavily counts.

Confidence in one sentence: it blends 45% field completeness, 25% crawl success, 20% contact proof tier (page regex, model guess, or search fallback), and 10% extraction cleanliness, minus a small penalty when names don't match page text. See `docs/PROJECT_EXPLAINED.md` for the full walkthrough with code pointers.

## Bonus features

Tavily fallback (`search_fallback.py`) fired on 2 of 3 sample domains: supabase.com appended Grant Huston and vapi.ai appended Gerard James Roy (each `appended=1 backfilled=0`), while postman.com needed nothing. Cost tracking prints per-domain tokens and USD on every live line and rolls them into `summary.total_tokens` / `summary.total_cost_usd`. Both are live-tested; full logs and hermetic tests live in `docs/BONUS_VERIFICATION.md`.

## Output shape

```json
{
  "records": [
    {
      "domain": "postman.com",
      "company_overview": "...",
      "target_audience": "...",
      "contact_points": ["info@postman.com", "info-jp@postman.com", "accommodations@postman.com"],
      "leadership": [{"name": "Abhinav Asthana", "title": "CEO/Co-Founder", "linkedin_url": "https://linkedin.com/in/abhinavasthana"}],
      "confidence_score": 0.95,
      "errors": [],
      "leadership_sources": {}
    }
  ],
  "summary": {
    "total_tokens": 19584,
    "total_cost_usd": 0.001195,
    "total_runtime_s": 60.19,
    "domains_failed": 0,
    "tavily_calls": 2,
    "tavily_credits": 2
  }
}
```

Each `confidence_score` runs 0.0 to 1.0 (hard failures collapse to 0.05 with reasons in `errors`). `leadership_sources` tags every LinkedIn URL as `regex`, `llm_inferred`, or `search_fallback`. `total_cost_usd` covers the LLM only, and Tavily use is counted separately.

## Setup notes

Local runs need Python 3.11 (`py -3.11` on Windows). `requirements.txt` holds 10 locked `==` pins and nothing else:

```
httpx==0.28.1
playwright==1.62.0
trafilatura==1.10.0
beautifulsoup4==4.15.0
lxml==5.3.0
pydantic==2.13.5
openai==1.51.0
python-dotenv==1.2.3
pytest==9.1.1
tiktoken==0.14.0
```

No `groq` or `ollama` package is needed, since both paths share the OpenAI-compatible client. Docker uses `mcr.microsoft.com/playwright/python:v1.62.0-noble` (Playwright 1.62.0 matches the pin list), a non-root `pwuser`, and `init: true`.

## Env vars

Everything tunable lives in `config.py` and is overridden through `.env` (see `.env.example` for comments on each):

`MODEL_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL`, `OLLAMA_MODEL`, `OLLAMA_BASE_URL`, `BROWSER_USER_AGENT`, `FETCH_TIMEOUT_S`, `ESCALATE_MIN_CLEAN_CHARS`, `SUBPAGE_PATTERN`, `MAX_SUBPAGES`, `TOKEN_BUDGET`, `PAGE_PRIORITY`, `LLM_FIELD_COUNT`, `RATE_LIMIT_BACKOFF_S`, `TARGET_DOMAINS`, `OUTPUT_PATH`, `TAVILY_API_KEY`, `TAVILY_TIMEOUT_S`, `TAVILY_MAX_RESULTS`, `TAVILY_SEARCH_DEPTH`, `TAVILY_QUERY_TEMPLATE`, `TAVILY_INCLUDE_DOMAINS`, `TAVILY_INCLUDE_DOMAINS_MODE`, `TAVILY_ENABLED`

## Honesty notes

- Docker runs Python 3.12 while local work uses 3.11. Code stays 3.11-compatible, and the gap is stated here instead of hidden.
- Strict `json_schema` from `ExtractionPayload.model_json_schema()` (nested leaders list, all fields required, nothing extra allowed) was accepted first try by `openai/gpt-oss-20b` (HTTP 200 on 2026-09-12). No emitter patch was needed.
- `reasoning_effort` never shipped. It doesn't appear in `config.py` or `extractor.py`, was never sent to any API, and no value was kept.

## Known limitations

- Scores come from fixed Python math, not the model rating itself, because small chat models mix fluency with fact.
- There's no LangGraph, Browser-Use, or agent loop. Fixed HTTP-first plus conditional browser steps cost less and demo in 2 minutes.
- LangChain extraction isn't built either. Its plug-in spot is sketched in `NOT_BUILT_langchain_seam.py` (docstring only, never called), skipped to keep this small scope dependable.
- Docker can't pin a strict 3.11 runtime, since the Playwright base image ships 3.12 (see honesty notes above).
- Plain HTTP is assumed for the easy path. Hydrated pages like vapi.ai subpages escalate to the browser by design and say so in the logs.

## Rubric coverage

- Scraping 30%: homepage plus 5 subpages, HTTP-first with browser escalation, boilerplate stripped before any model call.
- LLM 25%: one strict-schema extraction per domain (overview, ICP, emails, leaders, all required) via Groq default plus Ollama fallback.
- Resilience 20%: per-domain try/except with partial fallback, 0 failed across the 3-domain sample, retries and backoff logged in `errors`.
- Code and docs 15%: pinned requirements, copy-paste Docker and local runs, this README plus `docs/PROJECT_EXPLAINED.md` and `docs/BONUS_VERIFICATION.md`.
- Loom 10%: 2 to 3 minute screencast (code map 30s, live run with cost lines 60s, output plus confidence 45s, limits and ops YES 15s).

## Submission

- GitHub repo deliverable: clean modules, pinned `requirements.txt`, this README.
- `output/output.json` sample for postman.com, supabase.com, vapi.ai (`{"records":[...],"summary":{...}}`); reruns default to `output.local.json`.
- Tavily bonus plus cost tracking documented above; proof in `docs/BONUS_VERIFICATION.md`.
- Loom link (2 to 3 min max, covering code map, live run, output with confidence, limits, and ops YES): add link here.
- Ops answer for the submission email: Are you 100% comfortable spending roughly 40% of your working hours on manual lead prospecting, email discovery, and account handling alongside your AI engineering tasks? (Yes / No) → **YES**
- Send to support@softwarebrio.com with subject `[AI Intern Submission] - [Your Full Name]` and include your LinkedIn profile line in the same email.
