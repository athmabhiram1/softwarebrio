"""extractor.py — ONE LLM call/domain, deterministic confidence."""
from __future__ import annotations

import json
import re
import time

import config
from models import CompanyRecord, ExtractionPayload, ValidationError
from openai import OpenAI
import httpx

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s\-\(\)]{6,}\d")


def _keep_contact(c: str) -> bool:
    s = c.strip()
    if EMAIL_RE.fullmatch(s):
        return True
    if PHONE_RE.search(s) and len(re.sub(r"\D", "", s)) >= 7:
        return True
    return False


def build_messages(
    cleaned_text: str,
    found_linkedin_urls: list[str],
    found_emails: list[str],
) -> list[dict[str, str]]:
    system = (
        "You are a lead-enrichment extractor. Extract company_overview "
        "(2 sentences), target_audience, contact_points, leadership "
        "(name/title/linkedin_url). never emit confidence_score or errors; "
        "map the provided regex-found URLs/emails to names/contact_points "
        "instead of inventing. Return ONLY valid JSON matching the schema."
    )
    user = (
        f"{cleaned_text}\n\n"
        f"REGEX_HINTS: linkedin={json.dumps(found_linkedin_urls)} "
        f"emails={json.dumps(found_emails)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def get_client() -> OpenAI:
    # Constraint: httpx>=0.28 removed the `proxies` kwarg that openai 1.51
    # passes when building its own default client — always inject one.
    if config.MODEL_PROVIDER == "groq":
        return OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=config.GROQ_API_KEY,
            http_client=httpx.Client(timeout=30.0),
        )
    # ollama path — use exactly config.OLLAMA_BASE_URL, api_key "ollama"
    return OpenAI(
        base_url=config.OLLAMA_BASE_URL,
        api_key="ollama",
        http_client=httpx.Client(timeout=30.0),
    )


def _is_429(exc: Exception) -> bool:
    try:
        resp = getattr(exc, "response", None)
        if resp is not None and getattr(resp, "status_code", None) == 429:
            return True
    except Exception:
        pass
    if getattr(exc, "status_code", None) == 429:
        return True
    if "429" in str(exc):
        return True
    return False


def call_once(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    schema: dict,
) -> tuple[str, int, int]:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        response_format={  # type: ignore[arg-type]
            "type": "json_schema",
            "json_schema": {
                "name": "ExtractionPayload",
                "strict": True,
                "schema": schema,
            },
        },
        temperature=0,
    )
    content: str = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    prompt_tokens = 0
    completion_tokens = 0
    if usage is not None:
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    return content, int(prompt_tokens), int(completion_tokens)


def _call_with_429(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    schema: dict,
) -> tuple[str | None, int, int, bool]:
    """429-aware wrapper around call_once. Returns (content, pt, ct, had_429_failure)."""
    total_pt = 0
    total_ct = 0
    last_content: str | None = None
    for attempt in range(3):  # 1 initial + 2 retries
        try:
            content, pt, ct = call_once(client, model, messages, schema)
            total_pt += pt
            total_ct += ct
            return content, total_pt, total_ct, False
        except Exception as e:
            if _is_429(e) and attempt < 2:
                sleep_s = config.RATE_LIMIT_BACKOFF_S * (2**attempt)
                # cap total sleeps ~45s (5+15=20 for base 5; 3 attempts would be 35)
                time.sleep(sleep_s)
                continue
            if _is_429(e):
                # exhausted 429 retries -> signal failure
                return None, total_pt, total_ct, True
            raise
    return last_content, total_pt, total_ct, True


def extract_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    schema: dict,
) -> tuple[ExtractionPayload | None, int, int, list[str]]:
    # first attempt (429 retries separate, not consuming schema retry)
    raw, pt1, ct1, failed_429 = _call_with_429(client, model, messages, schema)
    if failed_429 or raw is None:
        return None, pt1, ct1, ["llm call failed after 429 retries"]
    total_pt = pt1
    total_ct = ct1
    try:
        payload = ExtractionPayload.model_validate_json(raw)
        return payload, total_pt, total_ct, []
    except ValidationError as e:
        # one schema retry, 429 handling independent
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": f"Validation failed: {e.errors()}. Fix JSON to match schema. Return ONLY valid JSON.",
            },
        ]
        raw2, pt2, ct2, failed_429_2 = _call_with_429(
            client, model, retry_messages, schema
        )
        total_pt += pt2
        total_ct += ct2
        if failed_429_2 or raw2 is None:
            return None, total_pt, total_ct, [f"validation retry failed: {e.errors()}"]
        try:
            payload2 = ExtractionPayload.model_validate_json(raw2)
            return payload2, total_pt, total_ct, []
        except ValidationError as e2:
            return None, total_pt, total_ct, [f"validation failed: {e2.errors()}"]


def compute_confidence(
    f_pop: int,
    pages_ok: int,
    pages_total: int,
    contact_mode: str,
    degraded: int,
) -> float:
    p_ratio = (pages_ok / pages_total) if pages_total else 0
    c_val = {"regex": 1.0, "inferred": 0.5, "none": 0.0}[contact_mode]
    q_val = {0: 1.0, 1: 0.5}.get(degraded, 0.0)
    raw = (
        0.45 * (f_pop / config.LLM_FIELD_COUNT)
        + 0.25 * p_ratio
        + 0.20 * c_val
        + 0.10 * q_val
    )
    return round(min(1.0, max(0.0, raw)), 2)


def extract_domain(
    domain: str,
    cleaned_text: str,
    found_linkedin_urls: list[str],
    found_emails: list[str],
    pages_ok: int,
    pages_total: int,
    degraded: int = 0,
) -> tuple[CompanyRecord, dict[str, object]]:
    client = get_client()
    model = config.GROQ_MODEL if config.MODEL_PROVIDER == "groq" else config.OLLAMA_MODEL
    schema = ExtractionPayload.model_json_schema()
    messages = build_messages(cleaned_text, found_linkedin_urls, found_emails)
    payload, pt, ct, errs = extract_with_retry(client, model, messages, schema)
    # cost: Groq pricing assumption for gpt-oss-20b: $0.075/1M in, $0.30/1M out
    cost_usd = pt / 1_000_000 * 0.075 + ct / 1_000_000 * 0.30
    meta: dict[str, object] = {"prompt_tokens": pt, "completion_tokens": ct, "cost_usd": cost_usd, "model": model}
    if payload is None:
        rec = CompanyRecord(company_overview="", target_audience="", contact_points=[], leadership=[], confidence_score=0.05, errors=errs or ["extraction failed"], domain=domain)
        return rec, meta
    filtered = [c for c in payload.contact_points if _keep_contact(c)]
    payload.contact_points = filtered
    f_pop = int(bool(payload.company_overview.strip())) + int(bool(payload.target_audience.strip())) + int(len(filtered) > 0) + int(any(le.name.strip() or le.title.strip() or le.linkedin_url.strip() for le in payload.leadership))
    contact_mode = "regex" if (found_linkedin_urls or found_emails) else ("inferred" if filtered else "none")
    confidence = compute_confidence(f_pop, pages_ok, pages_total, contact_mode, degraded)
    rec = CompanyRecord.from_payload(payload, confidence_score=confidence, errors=[], domain=domain)
    for le in payload.leadership:
        if "linkedin.com/company/" in le.linkedin_url.lower():
            rec.errors.append(f"company-page URL attributed to a person: {le.name}")
            break
    return rec, meta
