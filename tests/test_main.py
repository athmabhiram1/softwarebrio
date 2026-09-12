import json
import re
import sys
import pytest


def test_batch_2ok_1failing(tmp_path, monkeypatch):
    import main
    import fetcher
    import extractor
    import cleaner
    from models import CompanyRecord

    async def fake_fetch(domain, client=None):
        html = "<html><body><article><p>" + ("hello world " * 200) + "</p></article></body></html>"
        url = f"https://{domain}"
        return {"domain": domain, "pages": {url: html}, "methods": {url: "httpx"}, "errors": []}

    async def fake_fetch_fail(domain, client=None):
        if domain == "fail.com":
            raise RuntimeError("fetch boom")
        return await fake_fetch(domain, client)

    def fake_extract(domain, cleaned_text, urls, emails, pages_ok, pages_total, degraded=0):
        # return success records for ok domains
        rec = CompanyRecord(company_overview="overview", target_audience="aud", contact_points=["a@b.com"], leadership=[], confidence_score=0.9, errors=[])
        meta = {"prompt_tokens": 10, "completion_tokens": 20, "cost_usd": 0.001, "model": "test"}
        return rec, meta

    monkeypatch.setattr(fetcher, "fetch_domain", fake_fetch_fail)
    monkeypatch.setattr(extractor, "extract_domain", fake_extract)
    # also ensure clean_page returns trafilatura for fallback calc
    monkeypatch.setattr(cleaner, "clean_page", lambda html: {"text": "cleaned text " * 50, "path": "trafilatura", "raw_len": len(html), "clean_len": 100})
    monkeypatch.setattr(cleaner, "concat_domain", lambda pages: {"text": "cleaned text " * 50, "found_linkedin_urls": [], "found_emails": []})
    monkeypatch.setattr(cleaner, "estimate_tokens", lambda text: 100)

    out = tmp_path / "out.json"
    # run batch
    import asyncio
    asyncio.run(main.run_batch(["ok1.com", "ok2.com", "fail.com"], str(out), provider="groq"))
    assert out.exists()
    data = json.loads(out.read_text())
    assert "records" in data and "summary" in data
    assert len(data["records"]) == 3
    assert data["summary"]["domains_failed"] == 1
    # shape check
    for r in data["records"]:
        assert "company_overview" in r
        assert "confidence_score" in r
        assert "errors" in r
    # failing domain has 0.05
    failed = [r for r in data["records"] if r["confidence_score"] == 0.05]
    assert len(failed) == 1
    assert len(failed[0]["errors"]) > 0


def test_live_print_format(tmp_path, monkeypatch, capsys):
    import main
    import fetcher
    import extractor
    import cleaner
    from models import CompanyRecord

    async def fake_fetch(domain, client=None):
        html = "<html><body><article><p>" + ("x " * 500) + "</p></article></body></html>"
        url = f"https://{domain}"
        return {"domain": domain, "pages": {url: html}, "methods": {url: "httpx"}, "errors": []}

    def fake_extract(domain, cleaned_text, urls, emails, pages_ok, pages_total, degraded=0):
        rec = CompanyRecord(company_overview="ov", target_audience="aud", contact_points=[], leadership=[], confidence_score=0.77, errors=[])
        meta = {"prompt_tokens": 11, "completion_tokens": 22, "cost_usd": 0.0025, "model": "m"}
        return rec, meta

    monkeypatch.setattr(fetcher, "fetch_domain", fake_fetch)
    monkeypatch.setattr(extractor, "extract_domain", fake_extract)
    monkeypatch.setattr(cleaner, "clean_page", lambda html: {"text": "cleaned " * 20, "path": "trafilatura", "raw_len": len(html), "clean_len": 100})
    monkeypatch.setattr(cleaner, "concat_domain", lambda pages: {"text": "cleaned " * 20, "found_linkedin_urls": [], "found_emails": []})
    monkeypatch.setattr(cleaner, "estimate_tokens", lambda text: 55)

    out = tmp_path / "out2.json"
    import asyncio
    asyncio.run(main.run_batch(["example.com"], str(out), provider="groq"))
    captured = capsys.readouterr().out
    # regex for exact format
    pattern = r"\[\d+/\d+\] .+: (httpx|httpx\+playwright) \| raw \d+\.\dKB → clean \d+\.\dKB \(~\d+ tok\) \| LLM \d+/\d+ tok \$\d+\.\d{4} \| conf \d+\.\d{2} \| \d+\.\d+s"
    assert re.search(pattern, captured), f"print format mismatch: {captured!r}"


def test_degraded_math(tmp_path, monkeypatch):
    import main
    import fetcher
    import extractor
    import cleaner
    from models import CompanyRecord

    # clean domain: trafilatura + httpx only
    # degraded domain: bs4 + playwright => degraded 2
    captured_degraded = {}

    async def fake_fetch(domain, client=None):
        html = "<html><body>hi</body></html>"
        url = f"https://{domain}"
        if domain == "clean.com":
            return {"domain": domain, "pages": {url: html}, "methods": {url: "httpx"}, "errors": []}
        else:
            # degraded: method playwright, path bs4
            return {"domain": domain, "pages": {url: html}, "methods": {url: "playwright"}, "errors": []}

    def fake_clean_bs4(html):
        return {"text": "short", "path": "bs4", "raw_len": len(html), "clean_len": 10}

    def fake_clean_traf(html):
        return {"text": "cleaned " * 30, "path": "trafilatura", "raw_len": len(html), "clean_len": 100}

    def fake_extract_capture(domain, cleaned_text, urls, emails, pages_ok, pages_total, degraded=0):
        captured_degraded[domain] = degraded
        # confidence reflects degraded: clean higher
        conf = 0.9 if degraded == 0 else 0.5 if degraded == 2 else 0.7
        rec = CompanyRecord(company_overview="ov", target_audience="aud", contact_points=[], leadership=[], confidence_score=conf, errors=[])
        meta = {"prompt_tokens": 5, "completion_tokens": 5, "cost_usd": 0.0001, "model": "m"}
        return rec, meta

    monkeypatch.setattr(fetcher, "fetch_domain", fake_fetch)
    # per-domain clean_page behavior
    def dispatch_clean(html):
        # decide based on call count? Instead patch inside run_batch logic: we need different per domain.
        # We'll make clean_page check html content - but both same html, so need another way.
        # Instead we will monkeypatch main's degraded logic by using separate domains with different paths via fetcher methods and cleaner paths.
        # We'll make cleaner.clean_page return bs4 for degraded.com and trafilatura for clean.com by inspecting call stack.
        # Simpler: patch cleaner.clean_page to return bs4 always for degraded test and check that degraded==2 is computed when both flags present.
        # For this test we will just make it return bs4, and rely on methods to differentiate.
        # But we need clean.com to have trafilatura and degraded.com bs4. We'll use a closure with domain tracking.
        return {"text": "x", "path": "bs4", "raw_len": 10, "clean_len": 10}

    # We need domain-aware clean_page: we will override main's per-page loop via cleaner.clean_page that inspects the html's domain? 
    #Simpler: directly test main._compute_degraded
    monkeypatch.setattr(extractor, "extract_domain", fake_extract_capture)
    monkeypatch.setattr(cleaner, "concat_domain", lambda pages: {"text": "cleaned", "found_linkedin_urls": [], "found_emails": []})
    monkeypatch.setattr(cleaner, "estimate_tokens", lambda text: 10)

    # test degraded computation directly
    # Simulate clean.com: page path trafilatura, method httpx => degraded 0
    monkeypatch.setattr(cleaner, "clean_page", lambda html: {"text": "t", "path": "trafilatura", "raw_len": 10, "clean_len": 10})
    out = tmp_path / "out_clean.json"
    import asyncio
    # clear captured
    captured_degraded.clear()
    asyncio.run(main.run_batch(["clean.com"], str(out), provider="groq"))
    conf_clean = json.loads(out.read_text())["records"][0]["confidence_score"]
    deg_clean = captured_degraded.get("clean.com")

    # now degraded.com: path bs4 + playwright => 2
    monkeypatch.setattr(cleaner, "clean_page", lambda html: {"text": "t", "path": "bs4", "raw_len": 10, "clean_len": 10})
    out2 = tmp_path / "out_degraded.json"
    captured_degraded.clear()
    asyncio.run(main.run_batch(["degraded.com"], str(out2), provider="groq"))
    conf_deg = json.loads(out2.read_text())["records"][0]["confidence_score"]
    deg_deg = captured_degraded.get("degraded.com")

    assert deg_clean == 0, f"expected 0 got {deg_clean}"
    assert deg_deg == 2, f"expected 2 got {deg_deg}"
    assert conf_clean > conf_deg, f"clean {conf_clean} should > degraded {conf_deg}"


def test_empty_domains_exits_2():
    import main
    with pytest.raises(SystemExit) as exc:
        main.parse_args(["--domains", ""])
    assert exc.value.code == 2
    # also test via main() entry with empty should exit 2
    # Missing domains case is covered by default - not error, but empty string must be 2
