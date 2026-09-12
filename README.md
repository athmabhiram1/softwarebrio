# lead-enrichment-agent

## 1. What it does

Autonomous Python pipeline that takes company domains, crawls their public web presence (homepage + up to 5 same-origin subpages matching `/about|team|company|contact|pricing|leadership`), cleans HTML to token-efficient text (trafilatura then BeautifulSoup, capped at 6000 tokens by page priority), and calls a single LLM per domain (Groq default `openai/gpt-oss-20b` via the OpenAI-compatible SDK, Ollama fallback via `MODEL_PROVIDER=ollama`) to extract structured intelligence: company overview (2 sentences), target audience/ICP, contact emails, leadership (name/title/LinkedIn), plus a deterministic confidence score. One domain never crashes the batch; output is a single JSON object with records and a summary.

Tested against the three assignment domains: **postman.com**, **supabase.com**, **vapi.ai**.

## 2. Setup

Requires Python 3.11 locally (Docker image is 3.12 — see Honesty notes). Use `py -3.11` on Windows:

```bat
py -3.11 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
playwright install
copy .env.example .env
```

Then edit `.env` and set your key:

```
GROQ_API_KEY=        # required for Groq path; get one free at console.groq.com
```

All other vars have sane defaults (see `.env.example`). Without a key the pipeline falls back to Ollama when `MODEL_PROVIDER=ollama` or logs a clear warning.

Verify requirements are the 10 locked pins (all `==`, no `groq`/`ollama` package):

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

## 3. Run locally

```bat
py -3.11 main.py --domains postman.com,supabase.com,vapi.ai --out output.local.json
```

- `--domains` is comma-separated.
- `--out` defaults to `output.local.json` (tracked sample is `output.json`; reruns should not overwrite it unless you pass `--out output.json`).
- Live per-domain terminal line: `[{i}/{n}] {domain}: {mix} | raw {r}KB -> clean {c}KB (~{t} tok) | LLM {in}/{out} tok ${cost} | conf {s} | {sec}s`.

## 4. Run with Docker

```bat
docker compose up --build
```

- Single-stage image from `mcr.microsoft.com/playwright/python:v1.62.0-noble` (Playwright 1.62.0 matches `requirements.txt`), non-root `USER pwuser`, `init: true`.
- Compose reads `.env` via `env_file`, mounts `./output:/app/output`, and sets `extra_hosts: ["host.docker.internal:host-gateway"]` so `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` works on Linux and is a no-op on Docker Desktop.
- Command inside the container: `python main.py --domains postman.com,supabase.com,vapi.ai --out /app/output/output.json`.

## 5. Output shape

`output.json` (and `output.local.json`) is a JSON object, never a bare list:

```json
{
  "records": [
    {
      "domain": "postman.com",
      "company_overview": "...",
      "target_audience": "...",
      "contact_points": ["support@postman.com"],
      "leadership": [{"name": "...", "title": "...", "linkedin_url": "https://linkedin.com/in/..."}],
      "confidence_score": 0.82,
      "errors": []
    }
  ],
  "summary": {
    "total_tokens": 12345,
    "total_cost_usd": 0.0123,
    "total_runtime_s": 42.5,
    "domains_failed": 0
  }
}
```

- `records[i].confidence_score` is `0.0–1.0` (failures collapse to `0.05` with `errors` explaining why).
- `records[i].errors` is `list[str]` (empty on success).
- `summary.domains_failed` counts records that fell back to the partial path; totals reconcile with per-domain live logs.

## 6. Confidence formula

Confidence is deterministic — the LLM never self-grades — computed in Python as `round(min(1.0, max(0.0, 0.45*F+0.25*P+0.20*C+0.10*Q)), 2)` where: `0.45*F` rewards field completeness (F = populated fields / 4: overview, audience, contacts, leadership — each present field adds 0.1125); `0.25*P` rewards crawl success (P = pages that returned usable cleaned text / pages attempted); `0.20*C` rewards contact provenance (C = 1.0 if any email/LinkedIn came from a raw-HTML regex hit, 0.5 if inferred only by the LLM, 0.0 if none); `0.10*Q` rewards pipeline quality (Q = 1.0 clean trafilatura path, 0.5 one degradation to BS4/recall, 0.0 both degraded or empty) — Loom sentence: "Confidence is not the LLM grading itself; it is four auditable signals — 45% what we filled, 25% what we fetched, 20% whether contacts were regex-proven, 10% how clean the extraction path was." Leadership name mismatches incur a proportional penalty `name_penalty = 0.20 * (len(missing_names) / len(payload.leadership))` applied only when `payload.leadership` is non-empty and `missing_names` non-empty as `round(max(0.05, confidence - name_penalty), 2)` (empty leadership → no penalty), replacing the previous flat `-0.20`.

## 7. Honesty notes

- **Python version**: Docker image `mcr.microsoft.com/playwright/python:v1.62.0-noble` runs Python 3.12 while local development is 3.11. Code is 3.11-compatible; the version is noted honestly rather than faking a pin.
- **Smoke-test provenance**: `Groq strict json_schema smoke test: HTTP 200 on 2026-09-12; reasoning_effort=low not yet sent (gate: only after strict passes, pending approval)` — Groq strict `json_schema` with `ExtractionPayload.model_json_schema()` (nested `list[LeadershipEntry]`, all-required + `additionalProperties:false` at every level) accepted first-try by `openai/gpt-oss-20b`, no emitter patch needed.
- **`reasoning_effort`**: `reasoning_effort="low"` was not added to `config.py` before the gate. Per the plan it will be tried once inside the same smoke-test call that validates strict `json_schema`; if the API returns 200 it will be kept as a default, if rejected the literal error will be pasted here and the param dropped, with this paragraph updated to explain why — never assumed.

## 8. Known limitations

- **No LLM self-rated confidence** — deterministic formula instead, because small chat models are miscalibrated and fluency is not evidence.
- **No LinkedIn/Tavily founder search** — deferred to the bonus phase after core runs Docker-green, since homepage evidence shows leadership names are customer quotes that need external verification to avoid misattribution.
- **No LangGraph/Browser-Use/agentic framework** — a fixed httpx-first + conditional-Playwright pipeline is deterministic, cheaper, and demoable in 2 minutes where an agent loop would add flake.
- **No LangChain extraction/orchestration (NOT-BUILT seam)** — where a LangChain layer could plug in later is documented in `NOT_BUILT_langchain_seam.py` (docstring only, zero imports, never called from any execution path); not built because the deterministic single-call pipeline is cheaper and more reliable for 3 fixed domains. A custom tool-calling loop was considered (a linear 3-tool ReAct loop doesn't need a framework per current framework-comparison guidance) but not built, to keep this branch's scope and risk minimal given the submission timeline.
- **No strict Python 3.11 runtime inside Docker** — base image `v1.62.0-noble` ships Python 3.12, so code is written 3.11-compatible and the version is noted honestly rather than faking a pin.
- **Homepage-only server render assumed for happy path** — vapi.ai-style hydrated subpages escalate to Playwright by design and are logged as such instead of pretending plain HTTP always suffices.

## 9. Submission checklist

- [ ] GitHub repository link (clean, modular code; `requirements.txt` pinned; `README.md` present)
- [ ] `requirements.txt` — 10 locked `==` pins (httpx, playwright, trafilatura, beautifulsoup4, lxml, pydantic, openai, python-dotenv, pytest, tiktoken)
- [ ] `README.md` — setup, env, local run, Docker (`docker compose up --build`), output shape, confidence `0.45*F` paragraph, honesty notes, Known limitations
- [ ] `output.json` — committed real-run sample for postman.com, supabase.com, vapi.ai (`{"records":[...],"summary":{...}}`); reruns write `output.local.json` by default
- [ ] Loom / screen recording (2–3 min max): code map (30s), terminal run with live cost lines (60s), `output.json` + confidence Loom sentence (45s), limitations + ops YES (15s)
- [ ] Ops question — answer in the submission email: `Are you 100% comfortable spending roughly 40% of your working hours on manual lead prospecting, email discovery, and account handling alongside your AI engineering tasks? (Yes / No)` → **YES**
- [ ] Email subject: `[AI Intern Submission] - [Your Full Name]`
- [ ] LinkedIn profile line included in the same email

## Env vars

All tunables live in `config.py` and are overridden via `.env` (see `.env.example` for every var with comments):

`MODEL_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL`, `OLLAMA_MODEL`, `OLLAMA_BASE_URL`, `BROWSER_USER_AGENT`, `FETCH_TIMEOUT_S`, `ESCALATE_MIN_CLEAN_CHARS`, `SUBPAGE_PATTERN`, `MAX_SUBPAGES`, `TOKEN_BUDGET`, `PAGE_PRIORITY`, `LLM_FIELD_COUNT`, `RATE_LIMIT_BACKOFF_S`, `TARGET_DOMAINS`, `OUTPUT_PATH`, `TAVILY_API_KEY`
