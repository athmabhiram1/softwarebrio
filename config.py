"""Central configuration for lead-enrichment-agent.

Every tunable lives here, read from the environment with sane defaults.
No other module may hardcode model names, timeouts, thresholds, or regexes.
Secrets (API keys) are read from the environment / .env — never committed.
"""
from __future__ import annotations

import os
import re

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv optional at import time; plain env still works
    pass


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- LLM provider selection -------------------------------------------------
# "groq" (default, free tier) or "ollama" (local fallback). Both go through
# the `openai` SDK OpenAI-compat client — there is no `groq`/`ollama` package.
# Model names are env vars, never hardcoded at call sites.
MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", "groq").strip().lower()
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
# Default suits host runs; docker-compose overrides to host.docker.internal.
OLLAMA_BASE_URL: str = os.getenv(
    "OLLAMA_BASE_URL", "http://localhost:11434/v1"
)

# --- Fetching ---------------------------------------------------------------
BROWSER_USER_AGENT: str = os.getenv(
    "BROWSER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
FETCH_TIMEOUT_S: int = _get_int("FETCH_TIMEOUT_S", 10)
# Post-clean escalation: Playwright iff cleaned visible text is thinner than
# this OR status is 403/429/5xx. Never evaluated on raw HTML size.
ESCALATE_MIN_CLEAN_CHARS: int = _get_int("ESCALATE_MIN_CLEAN_CHARS", 500)

# --- Crawl scope -------------------------------------------------------------
SUBPAGE_RE: re.Pattern[str] = re.compile(
    os.getenv(
        "SUBPAGE_PATTERN",
        r"/(?:about|team|company|contact|pricing|leadership)(?:[/?#]|$)",
    ),
    re.IGNORECASE,
)
MAX_SUBPAGES: int = _get_int("MAX_SUBPAGES", 5)

# --- Cleaning / token budget --------------------------------------------------
TOKEN_BUDGET: int = _get_int("TOKEN_BUDGET", 6000)
# Highest-first page priority for trimming (fix 3). Lowest is dropped first.
PAGE_PRIORITY: list[str] = [
    s.strip().lower()
    for s in os.getenv(
        "PAGE_PRIORITY",
        "team,leadership,contact,about,company,homepage,pricing",
    ).split(",")
    if s.strip()
]

# --- Extraction ---------------------------------------------------------------
# 4 LLM-extracted fields (fix 1): overview, audience, contacts, leadership.
LLM_FIELD_COUNT: int = _get_int("LLM_FIELD_COUNT", 4)
# Base seconds for the 429 backoff schedule (5s, 15s; capped ~45s total).
RATE_LIMIT_BACKOFF_S: int = _get_int("RATE_LIMIT_BACKOFF_S", 5)

# --- Run inputs/outputs -------------------------------------------------------
TARGET_DOMAINS: list[str] = [
    d.strip().lower()
    for d in os.getenv(
        "TARGET_DOMAINS", "postman.com,supabase.com,vapi.ai"
    ).split(",")
    if d.strip()
]
# Tracked real-run sample is output.json; local reruns default elsewhere.
OUTPUT_PATH: str = os.getenv("OUTPUT_PATH", "output.local.json")

# --- Bonus seam (NOT-BUILT): search fallback -----------------------------------
# Read here so the seam needs zero rework later; never called by core.
TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY")
