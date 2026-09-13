# LANGCHAIN_RESEARCH.md — Agentic-layer verdict (report-only, no build)

> Date: 2026-09-12 · Report-only synthesis from 9 verified repo rows (librarian
> survey), Context7 `langchain.agents.create_agent` v1 API docs, and calibrated
> project history. No code changed to produce this file.

## 1) Repo Survey (verified rows, stars/last-commit as stated)

| Repo | Stars* | Last commit | Status | Pins / Stack | Graph shape | Tool / backend surface | Size / notes |
|---|---|---|---|---|---|---|---|
| `langchain-ai/company-researcher` | 215* | 2026-01-26 | ARCHIVED | `langgraph>=0.2.52` | hand-rolled `StateGraph`, 4 nodes | 1 Tavily backend | ~200-line loop |
| `langchain-ai/data-enrichment` | 253* | 2026-08-28 | maintained | `langgraph>=1.0.0,<2.0` + `langchain>=1.3.10` + `langchain-tavily>=0.1` | `StateGraph` + `ToolNode` | ~250-line loop, 2 tools (`TavilySearch` + `scrape_website`) | maintained reference for search+scrape enrichment |
| `guy-hartstein/company-research-agent` | 2274* | 2026-09-08 | maintained | `langchain==1.3.9` + `tavily-python==0.7.24` | `StateGraph`, 10 nodes | large fan-out multi-agent (NOT bounded) | most-starred; broadest surface, unbounded fan-out |
| `ankitkr777/lead-enrichment-agent` | 0* | 2026-09-12 | single-day toy | `langgraph>=0.2` + `langchain-groq` + `tavily-python>=0.3` + `playwright` | 4-node graph, ~150 lines | 1 search fn + 1 crawl, hardcoded `postman`/`supabase`/`vapi.ai` domains | closest domain match to THIS project, but toy-grade, single-day |
| `ayush-s-tomar/salesagent` | 2* | 2026-08-26 | maintained | `langgraph==0.2.28` + `groq==0.11.0` + `tavily-python==0.5.0` | bespoke ReAct `run_with_tools` (`max_iterations=10`) + 5-node graph | 4 Tavily-backed tools | explicit iteration bound (10); ReAct + graph hybrid |
| `brightdata/open-enrich` | 11* | 2026-05-30 | TS monorepo | `@langchain/langgraph ^1.2.8` + `deepagents ^1.9.0` | Deep Agents harness | swarm / fan-out, large surface | TypeScript; not directly portable to this Python pin set |
| `krishiawasthi/company-research-agent` | 0* | 2026-09-03 | DEPRECATED | `langchain.agents create_react_agent` + `AgentExecutor (max_iterations=12)` | ~50 lines | 1 DuckDuckGo tool | deprecated prebuilt path |
| `dsembiante/company-research-agent` | 0* | 2026-03-12 | stale, DEPRECATED | `create_tool_calling_agent` + `AgentExecutor (max_iterations=15)` | ~140 lines | 4 tools | deprecated prebuilt path, stale |
| `Psahu1296/research-agent` | 0* | 2026-05-24 | quiet | `StateGraph` supervisor/searcher/analyst, ~40 lines | 3-role graph | 1 DuckDuckGo backend | minimal supervisor pattern |

**Key negative finding:** NO sampled Python repo uses the current `langchain.agents.create_agent` v1 API. Only deprecated prebuilt paths appear (`create_react_agent`, `create_tool_calling_agent` + `AgentExecutor`). Any `create_agent` adoption here would be greenfield relative to this sample — no copy-paste reference implementation exists in the surveyed set. (Current API confirmed separately via Context7 reference docs: `from langchain.agents import create_agent(model, tools, system_prompt, middleware, response_format)` returning a compiled loop-until-no-tool-calls graph.)

Existing fallback in THIS project (for contrast): `search_tavily` (`httpx` POST, `basic` / 5 results / LinkedIn filter) → parse → enrich with provenance-tagged confidence; proven live (HTTP 200, real appends). `NOT_BUILT` seam stays regardless.

## 2) Best Architecture for THIS Project (if built)

Constraints (frozen): 3 fixed domains (`postman.com` / `supabase.com` / `vapi.ai`); 1–2 tools only (Tavily search + the existing single Groq `strict-json_schema` extraction call); that extraction call **must NOT be touched** — same contract, same trigger point as the existing fallback.

- **Nodes / agents:** single `create_agent` compiled graph. No supervisor, no sub-agents, no fan-out. One agent, one loop. The sampled large fan-out designs are explicitly rejected for 3 fixed domains.
- **State:** agent's built-in message/tool-call state only. No custom `StateGraph` schema. Input: `{company, domain}`; output: tool-grounded draft passed to the frozen extractor. `response_format` unset/passthrough — the frozen Groq strict-schema call remains the sole structured-output gate.
- **Tools (max 2):**
  1. `tavily_search` — thin wrapper over the existing proven `search_tavily` semantics (basic, 5 results, same LinkedIn filter, `httpx` POST). Reuse, don't rewrite.
  2. (optional, default OFF) `fetch_page` — single-URL fetch reusing the existing parser. No Playwright tool, no scrape suite.
- **Iteration bound:** hard cap — max 3 tool iterations (1 search + up to 2 fetches), then force-stop to extractor. An unbounded loop-until-no-tool-calls default is unacceptable.
- **Interface adapters (frozen extraction contract untouched):**
  - Opt-in env flag, default OFF. No behavior change unless explicitly enabled.
  - Same trigger point as the existing fallback; output fed *into* the untouched Groq strict-schema extraction call — never around it, never replacing it.
  - Provenance preserved: agent-sourced snippets carry the same provenance-tagged confidence path, so downstream scoring is blind to which upstream produced them.
  - Kill switch: any agent exception / empty result / bound hit → immediate silent fallthrough to the existing deterministic `search_tavily` path.

What is NOT proposed: multi-agent swarm, 10-node graph, Deep Agents harness, Playwright-as-tool, DuckDuckGo backends, deprecated `AgentExecutor` paths, or any change to pins / extractor / scoring.

## 3) Realistic Time Estimate (not optimistic)

Calibrated against the ACTUAL Tavily fallback cost in this project: a single deterministic call took multiple RED→GREEN cycles, live-gate debugging, recall tuning, and parser hardening across ~30 turns — and that was the *easy* version (no loop, no model-driven tool choice, fully reproducible). An agent loop multiplies nondeterminism on top.

| Phase | Work | Realistic cost |
|---|---|---|
| Scaffolding + pins | greenfield `create_agent` wiring (no in-sample reference), dep resolution against frozen pins, flag + adapter + kill-switch plumbing | 0.5–1 day, mostly pin fight |
| Determinism hardening | iteration cap, force-stop-to-extractor, flaky-tool-call retries, prompt pinning, provenance tagging parity | 0.5–1 day |
| Smoke-test gate (real APIs) | live Tavily + Groq runs per domain with repeats for variance; debug nondeterministic failures; parity check vs current fallback | 1–2 days (dominant cost) |
| Fresh-clone reproducibility | clean venv / fresh clone install from pins, lockfile proof, runbook | 0.5 day |
| Docker regression | image rebuild, full suite + live-gate subset in container, pin-drift check | 0.5 day |
| **Total** | | **3–5 days wall-clock, minimum 3** |

An "afternoon spike" only proves the loop runs once on one domain — not boundedness, parity, reproducibility, or container-clean behavior. Anything below 3 days assumes the agent loop is *easier to stabilize than a single HTTP call*, which contradicts project evidence.

## 4) Pin Risk vs Frozen Pins

Frozen: `trafilatura==1.10.0`, `openai==1.51.0`, `lxml==5.3.0`, `httpx==0.28.1`, `playwright==1.62.0`, `pydantic==2.13.5`.

- `langchain==1.3.x` pulls `langchain-core>=1.6,<2.0`, `langgraph>=1.2.11,<1.3.0`, `langsmith` + checkpoint packages (~15–20 transitive dependencies) — a second dependency universe alongside the frozen set, each transitive pin a fresh conflict surface.
- `langchain-openai`-era code expects OpenAI 2.x client semantics; frozen `openai==1.51.0` forces a pin break (upgrade → re-proof every OpenAI-adjacent path) or version-shim surgery. Either way the proven Groq strict-mode extraction call must be fully re-proofed live — client-major bumps are exactly the class of change that silently alters strict-schema validation behavior.
- `pydantic==2.13.5` is in-range for the `langchain-core` 1.6 line (OK) — the one non-problem; it does not offset the above.
- Net: adopting `langchain==1.3.x` breaks the frozen-pin guarantee the submission was stabilized against. Re-freezing means a new lockfile, fresh-clone proof, and Docker rebuild — all priced into §3, none free.

## 5) Verdict

**Not-worth-building as a post-submission optional branch — worth-only-if-X below.**

- Already outside the graded rubric: no bonus row for a LangChain agent in actual scoring.
- The bullet's letter and spirit are already satisfied twice over by shipped, live-proven work: (a) cost tracking, and (b) the working Tavily fallback with live proof (HTTP 200, real appends, provenance-tagged confidence).
- An agent loop adds unbounded-run risk, ~15–20 transitive deps, and a forced `openai==1.51.0` pin break against zero scoring upside — cost without credit.

**Worth-only-if-X:** build it only if ALL hold: (1) post-submission branch only, never touching the graded path; (2) full §3 gate completed (live smoke on all 3 domains, fresh-clone + Docker green); (3) frozen extractor contract byte-identical with kill-switch fallthrough proven; (4) a named consumer exists (demo, portfolio write-up, or explicit reviewer ask) — not speculative optionality. Absent all four, leave the `NOT_BUILT` seam as-is; the deterministic fallback is the better engineering answer for 3 fixed domains.

---
*Confirmation: no code file was modified to produce this report — synthesis and writing only (librarian survey, Context7 v1 API docs, ultrabrain synthesis).*
