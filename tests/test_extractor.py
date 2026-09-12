import json
import time

import config
import extractor
from extractor import build_messages, call_once, compute_confidence, extract_domain, extract_with_retry, get_client
from models import ExtractionPayload


# ---------- fakes ----------

class FakeUsage:
    def __init__(self, pt=10, ct=20):
        self.prompt_tokens = pt
        self.completion_tokens = ct


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class FakeChoice:
    def __init__(self, content: str):
        self.message = FakeMessage(content)


class FakeResp:
    def __init__(self, content: str, pt=10, ct=20, with_usage=True):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(pt, ct) if with_usage else None


class Fake429(Exception):
    def __init__(self, msg="429"):
        super().__init__(msg)
        self.response = type("R", (), {"status_code": 429})()
        self.status_code = 429


class FakeCompletions:
    def __init__(self, seq):
        # seq: list of either FakeResp or Exception
        self.seq = list(seq)
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        self.calls += 1
        item = self.seq.pop(0) if self.seq else self.seq[-1] if self.seq else None
        if isinstance(item, Exception):
            raise item
        return item


class FakeChat:
    def __init__(self, seq):
        self.completions = FakeCompletions(seq)


class FakeClient:
    def __init__(self, seq):
        self.chat = FakeChat(seq)


VALID_PAYLOAD = {
    "company_overview": "Acme does X. It serves Y.",
    "target_audience": "Developers building APIs",
    "contact_points": ["support@real.io"],
    "leadership": [{"name": "Alice", "title": "CEO", "linkedin_url": "linkedin.com/in/alice"}],
}
VALID_JSON = json.dumps(VALID_PAYLOAD)
INVALID_JSON = json.dumps({"company_overview": "only one field"})  # fails validation
INVALID_JSON2 = json.dumps({"bad": "data"})


def test_build_messages_forbids_confidence():
    msgs = build_messages("hello world", ["linkedin.com/in/bob"], ["a@b.com"])
    assert len(msgs) == 2
    assert "never emit confidence_score or errors" in msgs[0]["content"]
    assert "map the provided regex-found URLs/emails" in msgs[0]["content"]
    assert "REGEX_HINTS:" in msgs[1]["content"]
    assert "linkedin=" in msgs[1]["content"]
    assert "emails=" in msgs[1]["content"]


def test_get_client_groq_and_ollama(monkeypatch):
    captured = {}

    class DummyOpenAI:
        def __init__(self, base_url=None, api_key=None, **kw):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            self.base_url = base_url

    monkeypatch.setattr(extractor, "OpenAI", DummyOpenAI)
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    orig = config.MODEL_PROVIDER
    try:
        config.MODEL_PROVIDER = "groq"
        c = get_client()
        assert "groq.com" in captured["base_url"]
        assert captured["api_key"] == "test-key"
        config.MODEL_PROVIDER = "ollama"
        captured.clear()
        c2 = get_client()
        assert captured["base_url"] == config.OLLAMA_BASE_URL
        assert captured["api_key"] == "ollama"
    finally:
        config.MODEL_PROVIDER = orig


def test_call_once_schema_and_usage():
    schema = ExtractionPayload.model_json_schema()
    fake = FakeClient([FakeResp(VALID_JSON, pt=7, ct=13)])
    content, pt, ct = call_once(fake, "test-model", [{"role": "user", "content": "hi"}], schema)
    assert content == VALID_JSON
    assert pt == 7 and ct == 13
    # check response_format
    kwargs = fake.chat.completions.last_kwargs
    assert kwargs["temperature"] == 0
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["name"] == "ExtractionPayload"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    # usage missing -> defaults 0,0
    fake2 = FakeClient([FakeResp(VALID_JSON, with_usage=False)])
    _, pt2, ct2 = call_once(fake2, "m", [], schema)
    assert pt2 == 0 and ct2 == 0


def test_valid_json_record_and_hand_computed_confidence(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    schema = ExtractionPayload.model_json_schema()
    # pages_ok 3/4, found emails non-empty -> regex mode, degraded 0
    # F = 4 (all fields populated) => 0.45*1=0.45, P=0.75*0.25=0.1875, C=0.2, Q=0.1 => 0.9375->0.94
    fake = FakeClient([FakeResp(VALID_JSON, pt=10, ct=20)])
    # monkeypatch get_client inside extract_domain
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    monkeypatch.setattr(config, "MODEL_PROVIDER", "groq")
    rec, meta = extract_domain("example.com", "cleaned text Alice", ["linkedin.com/in/alice"], ["a@b.com"], 3, 4)
    assert rec.company_overview == VALID_PAYLOAD["company_overview"]
    assert rec.errors == []
    expected = compute_confidence(4, 3, 4, "regex", 0)
    # hand compute
    hand = round(min(1.0, max(0.0, 0.45 * (4 / config.LLM_FIELD_COUNT) + 0.25 * (3 / 4) + 0.20 * 1.0 + 0.10 * 1.0)), 2)
    assert hand == 0.94, f"hand {hand}"
    assert expected == hand == 0.94
    assert rec.confidence_score == expected == 0.94
    print(f"hand-computed confidence valid case: F=4 P=0.75 C=regex Q=1.0 => {hand}")
    assert meta["prompt_tokens"] == 10
    assert meta["completion_tokens"] == 20
    # cost: 10/1e6*0.075 +20/1e6*0.30
    assert abs(meta["cost_usd"] - (10 / 1e6 * 0.075 + 20 / 1e6 * 0.30)) < 1e-9


def test_invalid_then_valid_exactly_2_calls(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    schema = ExtractionPayload.model_json_schema()
    fake = FakeClient([FakeResp(INVALID_JSON), FakeResp(VALID_JSON)])
    msgs = build_messages("text", [], [])
    payload, pt, ct, errs = extract_with_retry(fake, "m", msgs, schema)
    assert payload is not None
    assert fake.chat.completions.calls == 2
    assert payload.company_overview == VALID_PAYLOAD["company_overview"]


def test_invalid_twice_partial(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    fake = FakeClient([FakeResp(INVALID_JSON), FakeResp(INVALID_JSON2)])
    msgs = build_messages("text", [], [])
    schema = ExtractionPayload.model_json_schema()
    payload, pt, ct, errs = extract_with_retry(fake, "m", msgs, schema)
    assert payload is None
    assert fake.chat.completions.calls == 2
    # via extract_domain -> 0.05 + errors
    fake2 = FakeClient([FakeResp(INVALID_JSON), FakeResp(INVALID_JSON2)])
    monkeypatch.setattr(extractor, "get_client", lambda: fake2)
    rec, meta = extract_domain("example.com", "text", [], [], 0, 1)
    assert rec.confidence_score == 0.05
    assert len(rec.errors) > 0
    assert fake2.chat.completions.calls == 2


def test_429_twice_then_valid_schema_retry_untouched(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    # sequence: 429,429,invalid,valid => 4 calls, proves 429 retries separate from schema retry
    seq = [Fake429(), Fake429(), FakeResp(INVALID_JSON), FakeResp(VALID_JSON)]
    fake = FakeClient(seq)
    msgs = build_messages("text", [], [])
    schema = ExtractionPayload.model_json_schema()
    payload, pt, ct, errs = extract_with_retry(fake, "m", msgs, schema)
    assert payload is not None
    assert fake.chat.completions.calls == 4, f"got {fake.chat.completions.calls}"
    # also test 429-twice-then-valid alone succeeds with 3 calls, then later invalid still gets retry => total 4 if counted differently
    # second scenario: two separate extract_with_retry calls on same fake queue
    fake2 = FakeClient([Fake429(), Fake429(), FakeResp(VALID_JSON), FakeResp(INVALID_JSON), FakeResp(VALID_JSON)])
    # first logical extraction: 429 twice then valid = 3 calls
    p1, _, _, _ = extract_with_retry(fake2, "m", msgs, schema)
    assert p1 is not None
    assert fake2.chat.completions.calls == 3
    # second logical extraction with invalid then valid should still get its retry (independent counter)
    p2, _, _, _ = extract_with_retry(fake2, "m", msgs, schema)
    assert p2 is not None
    assert fake2.chat.completions.calls == 5  # 3 +2
    # But spec asserts total calls ==4 for combined 429+schema case; we already proved 4 above
    print("429 separate from schema retry verified: 4 calls for 429x2+invalid+valid")


def test_formula_unit_cases():
    # all-perfect -> 1.0 : F=4, P=1, C=regex, Q=1.0 => 0.45+0.25+0.2+0.1=1.0
    assert compute_confidence(4, 4, 4, "regex", 0) == 1.0
    print(f"all-perfect confidence: {compute_confidence(4,4,4,'regex',0)} expected 1.0")
    # all-failed but degraded 2 and none contact and 0 pages => raw =0 +0+0+0=0 -> clamped 0.0 then extract_domain maps to 0.05 on failure
    # For compute_confidence itself all-failed F=0 P=0 C=none Q=0 => 0.0
    assert compute_confidence(0, 0, 4, "none", 2) == 0.0
    # extract_domain all-failed maps to 0.05 (tested above) ; here test raw formula is 0.0
    # But spec says all-failed -> 0.05 ; that is via extract_domain path, compute gives 0.0
    # Test pages_total 0 -> no ZeroDivision, P=0
    assert compute_confidence(2, 0, 0, "inferred", 1) == round(0.45 * (2 / 4) + 0.25 * 0 + 0.20 * 0.5 + 0.10 * 0.5, 2)
    # ensure no exception
    try:
        compute_confidence(0, 0, 0, "none", 0)
    except ZeroDivisionError:
        assert False, "ZeroDivision on pages_total 0"
    print(f"pages_total 0 confidence: {compute_confidence(2,0,0,'inferred',1)}")
    # degraded mapping 0->1.0,1->0.5,2->0.0
    assert compute_confidence(4, 4, 4, "regex", 0) == 1.0
    assert compute_confidence(4, 4, 4, "regex", 1) == 0.95  # 1.0 -0.05
    assert compute_confidence(4, 4, 4, "regex", 2) == 0.90


def test_contacts_filtered_to_email_and_phone(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["https://linkedin.com/company/x", "Discord community support", "sales@x.com", "+1 415 123 4567"],
        "leadership": [{"name": "Alice", "title": "CEO", "linkedin_url": "https://linkedin.com/in/alice"}],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    rec, meta = extract_domain("example.com", "text Alice", [], [], 1, 1)
    assert rec.contact_points == ["sales@x.com"]
    print(f"filtered contacts: {rec.contact_points}")
    assert rec.confidence_score == compute_confidence(4, 1, 1, "inferred", 0)


def test_contacts_filter_recomputes_F_when_all_invalid(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["https://linkedin.com/company/x", "Discord community support"],
        "leadership": [{"name": "Alice", "title": "CEO", "linkedin_url": "https://linkedin.com/in/alice"}],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    rec, meta = extract_domain("example.com", "Alice text", [], [], 1, 1)
    assert rec.contact_points == []
    expected = compute_confidence(3, 1, 1, "none", 0)
    assert rec.confidence_score == expected
    print(f"all-invalid filtered contacts: {rec.contact_points} F=3 conf={expected}")


def test_leadership_company_page_flag(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com"],
        "leadership": [{"name": "J", "title": "VP", "linkedin_url": "https://linkedin.com/company/acme"}],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    rec, meta = extract_domain("example.com", "J text", [], [], 1, 1)
    assert any("company-page URL attributed to a person: J" in e for e in rec.errors)
    assert rec.confidence_score == compute_confidence(4, 1, 1, "inferred", 0)
    print(f"company-page errors: {rec.errors} conf={rec.confidence_score}")


def test_prepass_normalizes_and_dedupes():
    from cleaner import regex_prepass

    html = '<a href="linkedin.com/in/x">one</a> <a href="https://linkedin.com/in/x">two</a> <a href="LINKEDIN.COM/in/Abhinav-Asthana">three</a>'
    result = regex_prepass(html)
    li = result["linkedin_urls"]
    assert "https://linkedin.com/in/x" in li
    assert li.count("https://linkedin.com/in/x") == 1
    assert "https://LINKEDIN.COM/in/Abhinav-Asthana" in li
    assert len(li) == 2
    print(f"prepass normalized: {li}")


def test_domain_threaded_end_to_end(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    fake = FakeClient([FakeResp(VALID_JSON)])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    rec, meta = extract_domain("acme.com", "cleaned text Alice", ["linkedin.com/in/alice"], ["a@b.com"], 3, 4)
    assert rec.domain == "acme.com"
    print(f"domain threaded: {rec.domain}")
    from models import CompanyRecord

    rec2 = CompanyRecord(company_overview="o", target_audience="t", contact_points=[], leadership=[], confidence_score=0.5, errors=[])
    assert rec2.domain == ""
    from models import ExtractionPayload

    payload = ExtractionPayload.model_validate(VALID_PAYLOAD)
    rec3 = CompanyRecord.from_payload(payload, confidence_score=0.9, errors=[])
    assert rec3.domain == ""
    rec4 = CompanyRecord.from_payload(payload, confidence_score=0.9, errors=[], domain="example.org")
    assert rec4.domain == "example.org"


def test_contacts_email_only_hardening(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com", "+1 415 796 6470", "1-877-HEY-VAPI"],
        "leadership": [{"name": "Alice", "title": "CEO", "linkedin_url": "https://linkedin.com/in/alice"}],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    rec, meta = extract_domain("example.com", "text Alice", [], [], 1, 1)
    assert rec.contact_points == ["sales@x.com"]
    print(f"email-only hardening: {rec.contact_points} -> expected ['sales@x.com']")


def test_leadership_name_present_no_penalty(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com"],
        "leadership": [{"name": "Alice Johnson", "title": "CEO", "linkedin_url": "https://linkedin.com/in/alice"}],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    cleaned = "Our CEO Alice Johnson leads the company. Acme does X."
    rec, meta = extract_domain("example.com", cleaned, ["linkedin.com/in/alice"], ["a@b.com"], 3, 4)
    assert rec.errors == []
    assert rec.confidence_score == 0.94
    print(f"name present: errors={rec.errors} conf={rec.confidence_score} expected 0.94 no penalty")


def test_leadership_name_absent_penalty(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com"],
        "leadership": [{"name": "Alice Johnson", "title": "CEO", "linkedin_url": "https://linkedin.com/in/alice"}],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    cleaned = "No leadership mentioned here, just product description."
    rec, meta = extract_domain("example.com", cleaned, ["linkedin.com/in/alice"], ["a@b.com"], 3, 4)
    assert any("name not found in source text - possible LLM invention: Alice Johnson" in e for e in rec.errors)
    assert rec.confidence_score == 0.74, f"got {rec.confidence_score} expected 0.74"
    print(f"name absent: errors={rec.errors} conf={rec.confidence_score} expected 0.74 (0.94-0.20)")


def test_leadership_empty_name_skipped(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com"],
        "leadership": [{"name": "   ", "title": "CEO", "linkedin_url": "https://linkedin.com/in/alice"}],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    rec, meta = extract_domain("example.com", "cleaned text", [], [], 1, 1)
    assert rec.errors == []
    expected = compute_confidence(4, 1, 1, "inferred", 0)
    assert rec.confidence_score == expected
    print(f"empty name skipped: errors={rec.errors} conf={rec.confidence_score} expected {expected}")


def test_company_flag_and_name_flag_coexist(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com"],
        "leadership": [{"name": "Bob Smith", "title": "CTO", "linkedin_url": "https://linkedin.com/company/acme"}],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    cleaned = "Product description without Bob Smith"
    cleaned_no_name = "Product description without any leader"
    rec, meta = extract_domain("example.com", cleaned_no_name, [], [], 1, 1)
    assert any("company-page URL attributed to a person: Bob Smith" in e for e in rec.errors)
    assert any("name not found in source text - possible LLM invention: Bob Smith" in e for e in rec.errors)
    assert len([e for e in rec.errors if "Bob Smith" in e]) == 2
    base = compute_confidence(4, 1, 1, "inferred", 0)
    expected = round(max(0.05, base - 0.2), 2)
    assert rec.confidence_score == expected, f"got {rec.confidence_score} expected {expected} base {base}"
    print(f"coexist flags: errors={rec.errors} conf={rec.confidence_score} base={base} -> {expected}")


def test_empty_cleaned_text_skips_llm(monkeypatch, capsys):
    import main, fetcher, cleaner

    async def fake_fetch(domain, client=None):
        html = "<html><body></body></html>"
        url = f"https://{domain}"
        return {"domain": domain, "pages": {url: html}, "methods": {url: "httpx"}, "errors": ["fetch error: timeout"]}

    called = {"hit": False}

    def boom(*a, **kw):
        called["hit"] = True
        raise AssertionError("extract_domain must NOT be called on empty cleaned text")

    monkeypatch.setattr(fetcher, "fetch_domain", fake_fetch)
    monkeypatch.setattr(extractor, "extract_domain", boom)
    monkeypatch.setattr(cleaner, "concat_domain", lambda pages: {"text": "   ", "found_linkedin_urls": [], "found_emails": []})
    monkeypatch.setattr(cleaner, "clean_page", lambda html: {"text": "", "path": "bs4", "raw_len": len(html), "clean_len": 0})
    monkeypatch.setattr(cleaner, "estimate_tokens", lambda text: 0)

    import asyncio
    rec, tok, cost = asyncio.run(main._process_single("empty.com", 1, 1))
    assert not called["hit"], "extract_domain was called on empty cleaned text"
    assert rec.company_overview == ""
    assert rec.target_audience == ""
    assert rec.contact_points == []
    assert rec.leadership == []
    assert rec.confidence_score == 0.05
    assert "fetch error: timeout" in rec.errors
    assert not any("AssertionError" in e for e in rec.errors)
    assert tok == 0
    assert cost == 0.0
    out = capsys.readouterr().out
    assert "empty.com" in out
    assert "conf 0.05" in out
    assert "LLM 0/0" in out
    print(f"empty cleaned -> skip LLM: errors={rec.errors} tok={tok} cost={cost} conf={rec.confidence_score}")


def test_zero_populated_increments_domains_failed(tmp_path, monkeypatch):
    import main, fetcher, cleaner

    async def fake_fetch(domain, client=None):
        html = "<html></html>"
        url = f"https://{domain}"
        return {"domain": domain, "pages": {url: html}, "methods": {url: "httpx"}, "errors": []}

    monkeypatch.setattr(fetcher, "fetch_domain", fake_fetch)
    monkeypatch.setattr(cleaner, "concat_domain", lambda pages: {"text": "", "found_linkedin_urls": [], "found_emails": []})
    monkeypatch.setattr(cleaner, "clean_page", lambda html: {"text": "", "path": "bs4", "raw_len": 0, "clean_len": 0})
    monkeypatch.setattr(cleaner, "estimate_tokens", lambda text: 0)
    monkeypatch.setattr(extractor, "extract_domain", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")))

    import asyncio
    out = tmp_path / "out_empty.json"
    data = asyncio.run(main.run_batch(["empty1.com", "empty2.com"], str(out), provider="groq"))
    assert data["summary"]["domains_failed"] == 2
    for r in data["records"]:
        assert r["confidence_score"] == 0.05
        assert r["company_overview"] == ""
        assert r["target_audience"] == ""
        assert r["contact_points"] == []
        assert r["leadership"] == []
    print(f"zero-populated domains_failed={data['summary']['domains_failed']}")


def test_contact_hygiene_reserved_domains():
    from extractor import _keep_contact

    inputs = ["a@example.com", "b@example.org", "c@example.net", "d@foo.test", "e@bar.invalid", "f@x.localhost", "ok@real.io"]
    kept = [c for c in inputs if _keep_contact(c)]
    assert kept == ["ok@real.io"], f"got {kept}"
    assert not _keep_contact("x@Y.TEST")
    assert not _keep_contact("x@y.INVALID")
    assert not _keep_contact("x@y.LOCALHOST")
    print(f"contact hygiene reserved filtered -> {kept}")


def test_contact_hygiene_escape_and_entity_remnants():
    from extractor import _keep_contact

    assert not _keep_contact("me\\u003e@x.com")
    assert not _keep_contact("a\\u0041@x.com")
    assert not _keep_contact("a&amp;b@x.com")
    assert not _keep_contact("x&#39;@y.com")
    assert not _keep_contact("foo&test;@bar.com")
    assert _keep_contact("ok@real.io")
    assert _keep_contact("sales@company.com")
    print("escape/entity remnants rejected, valid kept")


def test_proportional_name_penalty_1_of_8(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    names = [f"Person {i}" for i in range(8)]
    leadership = [{"name": n, "title": "CEO", "linkedin_url": "https://linkedin.com/in/x"} for n in names]
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com"],
        "leadership": leadership,
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    cleaned = " ".join(names[:7])
    rec, meta = extract_domain("example.com", cleaned, ["linkedin.com/in/x"], ["a@b.com"], 3, 4)
    base = compute_confidence(4, 3, 4, "regex", 0)
    assert base == 0.94, f"base {base}"
    penalty = 0.20 * (1 / 8)
    expected = round(max(0.05, base - penalty), 2)
    assert rec.confidence_score == expected, f"got {rec.confidence_score} expected {expected} base {base} penalty {penalty}"
    print(f"1/8 penalty: base={base} penalty={penalty} -> {expected} got={rec.confidence_score}")


def test_proportional_name_penalty_8_of_8(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    names = [f"Person {i}" for i in range(8)]
    leadership = [{"name": n, "title": "CEO", "linkedin_url": "https://linkedin.com/in/x"} for n in names]
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com"],
        "leadership": leadership,
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    cleaned = "No leadership mentioned here, just product description."
    rec, meta = extract_domain("example.com", cleaned, ["linkedin.com/in/x"], ["a@b.com"], 3, 4)
    base = compute_confidence(4, 3, 4, "regex", 0)
    assert base == 0.94
    penalty = 0.20 * (8 / 8)
    expected = round(max(0.05, base - penalty), 2)
    assert rec.confidence_score == expected, f"got {rec.confidence_score} expected {expected}"
    print(f"8/8 penalty: base={base} penalty={penalty} -> {expected} got={rec.confidence_score}")


def test_proportional_name_penalty_empty_leadership_no_penalty(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    payload_dict = {
        "company_overview": "Acme does X. It serves Y.",
        "target_audience": "Developers",
        "contact_points": ["sales@x.com"],
        "leadership": [],
    }
    fake = FakeClient([FakeResp(json.dumps(payload_dict))])
    monkeypatch.setattr(extractor, "get_client", lambda: fake)
    cleaned = "Empty leadership case"
    rec, meta = extract_domain("example.com", cleaned, ["linkedin.com/in/x"], ["a@b.com"], 3, 4)
    base = compute_confidence(3, 3, 4, "regex", 0)
    assert rec.confidence_score == base
    print(f"empty leadership no penalty: base={base} got={rec.confidence_score}")


def test_regex_prepass_decodes_entities_and_unicode():
    from cleaner import regex_prepass

    html = "Contact: a&#64;b.com and c\\u0040d.com plus Sales&#64;Example.COM"
    result = regex_prepass(html)
    emails = result["emails"]
    assert "a@b.com" in emails, f"got {emails}"
    assert "c@d.com" in emails, f"got {emails}"
    print(f"prepass decoded emails={emails}")
