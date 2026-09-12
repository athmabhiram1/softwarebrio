# LOOM_SCRIPT.md — Lead Enrichment Agent (Todo 10)

> **Dry-read timing:** 2 min 08 s spoken (322w @150wpm = 128.8s) — all numbers quoted from `output/compose.log` + `output/output.json` + `README.md`. No invented numbers.

---

## 0. Recording checklist (before you hit record)

- Terminal full-screen, font 14+. Have ready: `output/compose.log`, `output/output.json`, `README.md §6`.
- Run: `docker compose up --build` OR `py -3.11 main.py --domains postman.com,supabase.com,vapi.ai --out output.local.json`.

---

## Block 1 — Code map (30s) ⏱ 0:00–0:30

> Speak: 6 modules + data flow fetch → clean → extract → output (one sentence each).

"Lead-enrichment pipeline — three domains. Six modules: config.py holds every tunable; fetcher.py is httpx-first with one shared Playwright that escalates only on thin clean text or 403/429; cleaner.py does regex pre-pass then trafilatura, BeautifulSoup fallback, 6000-token cap; extractor.py makes one Groq call per domain; models.py enforces strict Pydantic; main.py batches so one domain never kills the run. Flow: fetch crawls homepage plus five subpages, clean shrinks raw HTML to token budget, extract structures overview/audience/contacts/leadership, output writes one JSON object with records plus summary."

---

## Block 2 — Terminal run (60s) ⏱ 0:30–1:30

> Speak: replay the REAL 3 lines from `output/compose.log` — point at mix, raw→clean→tokens, $.

"Real Docker run — `docker compose up --build` — this is `output/compose.log`:"

```
[1/3] postman.com: httpx+playwright | raw 2320.2KB → clean 24.4KB (~5788 tok) | LLM 6098/821 tok $0.0007 | conf 0.95 | 23.3s
[2/3] supabase.com: httpx+playwright | raw 1925.7KB → clean 8.1KB (~2073 tok) | LLM 2405/592 tok $0.0004 | conf 0.84 | 16.7s
[3/3] vapi.ai: httpx | raw 406.3KB → clean 2.8KB (~629 tok) | LLM 1002/602 tok $0.0003 | conf 0.89 | 12.0s
```

"Postman and supabase both escalated — httpx+playwright — vapi stayed httpx. Raw to clean shows the token work: 2320K to 24K, 1925K to 8K. Summary from `output.json`: total_tokens 20010, total_cost_usd 0.001317 — about $0.001317, a tenth of a cent — total_runtime_s 51.97, domains_failed 0, confidences [0.95, 0.84, 0.89]."

---

## Block 3 — Output + confidence (45s) ⏱ 1:30–2:15

> Speak: open `output/output.json`, show one record, say the Loom sentence verbatim.

"Output is one object — records plus summary. Postman record:"

```json
{
  "company_overview": "Postman is the leading collaboration platform for API-development, used by more than 40 million users and 500,000 companies worldwide. Postman is an elegant, flexible environment used to build connected software via APIs, quickly, easily and accurately.",
  "target_audience": "Engineers, developers, and teams building, testing, and managing APIs, from individuals to enterprises.",
  "contact_points": ["info@postman.com", "info-jp@postman.com", "accommodations@postman.com", "+1 415 796 6470"],
  "leadership": [{"name": "Abhinav Asthana", "title": "CEO/Co-Founder", "linkedin_url": "linkedin.com/in/abhinavasthana"}],
  "confidence_score": 0.95,
  "errors": []
}
```

"Every record has confidence_score 0.0–1.0 and errors. Loom sentence verbatim from README §6: Confidence is not the LLM grading itself; it is four auditable signals — 45% what we filled, 25% what we fetched, 20% whether contacts were regex-proven, 10% how clean the extraction path was. Honest caveat: vapi.ai leadership entry came from a customer testimonial — Jason Mitura, VP of Software Development — flagged in errors as 'company-page URL attributed to a person' with conf 0.89 not 1.0 — predicted data-quality edge, covered in README limitations — say it, don't hide it."

---

## Block 4 — Limitations + ops YES (15s) ⏱ 2:15–2:30

> Speak: one limitation honestly + verbatim ops YES.

"Limitation: no external LinkedIn or Tavily founder verification yet — customer quotes can misattribute, so we deferred that versus faking precision. Ops question — Are you 100% comfortable spending roughly 40% of your working hours on manual lead prospecting, email discovery, and account handling alongside your AI engineering tasks? YES."

---

## Submission pack (paste verbatim into email)

```
Subject: [AI Intern Submission] - [Your Full Name]

GitHub repository: [PASTE YOUR GITHUB LINK HERE — e.g., https://github.com/<you>/lead-enrichment-agent]

LinkedIn profile: [PASTE YOUR LINKEDIN URL HERE — e.g., https://linkedin.com/in/<you>]

Ops question answer:
Are you 100% comfortable spending roughly 40% of your working hours on manual lead prospecting, email discovery, and account handling alongside your AI engineering tasks? (Yes / No)
Answer: YES

Attachments/links in repo:
- output/output.json (real run sample — 3 domains, confidences 0.95/0.84/0.89, total $0.001317, domains_failed 0)
- README.md (setup, Docker, output shape, confidence formula, honesty notes, limitations)
- Loom link: [PASTE LOOM URL — 2–3 min max: 30s map + 60s terminal + 45s output/confidence + 15s limitations/YES]
```

---

## QA — Word-count timing estimate (150 wpm)

> Printed via `py -3.11` — spoken text only (all quoted SAY blocks outside ``` fences). Code fences and headers excluded. Must total ≤180s.

```
Spoken words: 322
Seconds @150wpm: 128.8s (2m 08s)
≤180s? YES — headroom 51.2s / 128 words
Total file words: 981
```

Verification — dry-read check (§4) pasted:

```
spoken_quotes=6
 q1: 96w -> Lead-enrichment pipeline ...
 q2: 12w -> Real Docker run ...
 q3: 58w -> Postman and supabase both escalated ...
 q4: 9w -> Output is one object ...
 q5: 99w -> Every record has confidence_score ...
 q6: 48w -> Limitation: no external LinkedIn ...
SPOKEN_WORDS=322
SECONDS=128.8 (2m 08s)
LE_180=True headroom=51.2s
TOTAL_FILE=981
```

Verification command (run at repo root):

```bat
py -3.11 "C:\Users\athma\AppData\Local\Temp\opencode\wc_inclusive.py"
```

wc_inclusive.py removes ``` fences before counting, then counts all quoted SAY blocks in Blocks 1–4. Re-run after any edit — never exceed 450 spoken words (180s * 150wpm / 60).

---

## Dry-read notes

- Point at `httpx+playwright` vs `httpx` column, then raw→clean, then `$` column — don't read every digit.
- Full-screen the confidence sentence when you say it.
- Say the vapi caveat calmly, once — honesty-first wins reviewers.
