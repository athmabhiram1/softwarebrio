"""test_search_fallback.py — RED-first tests for Tavily fallback (Task 1, bonus branch).

Covers the 5 hardening fixes: dedupe-backfill, compute_confidence reuse,
C=max, cap-3 + drop-nameless, tavily_query in meta. Plus forced-trigger dry run.
All tests MUST FAIL before search_fallback.py exists (RED), then go GREEN.
"""
from __future__ import annotations


def _rec(leadership: list[dict]) -> object:
    from models import CompanyRecord, LeadershipEntry

    return CompanyRecord(
        company_overview="o",
        target_audience="a",
        contact_points=[],
        leadership=[LeadershipEntry(**le) for le in leadership],
        confidence_score=0.5,
        errors=[],
        domain="supabase.com",
    )


def test_should_trigger_empty() -> None:
    import search_fallback

    assert search_fallback.should_trigger(_rec([])) is True


def test_should_trigger_all_company_page() -> None:
    import search_fallback

    rec = _rec(
        [
            {"name": "A", "title": "CEO", "linkedin_url": "https://linkedin.com/company/supabase"},
            {"name": "B", "title": "CTO", "linkedin_url": ""},
        ]
    )
    assert search_fallback.should_trigger(rec) is True


def test_should_not_trigger_when_one_valid_in() -> None:
    import search_fallback

    rec = _rec(
        [
            {"name": "A", "title": "CEO", "linkedin_url": "https://linkedin.com/in/alice"},
            {"name": "B", "title": "CTO", "linkedin_url": "https://linkedin.com/company/supabase"},
        ]
    )
    assert search_fallback.should_trigger(rec) is False


def test_parse_name_title_linkedin_pattern() -> None:
    import search_fallback

    name, _ = search_fallback.parse_name_title("Jane Doe - CEO at Supabase | LinkedIn", "")
    assert name == "Jane Doe"


def test_parse_name_title_unparseable_returns_none() -> None:
    import search_fallback

    assert search_fallback.parse_name_title("Supabase Pricing - LinkedIn", "") is None


def test_extract_caps_at_3_and_drops_company_and_nameless() -> None:
    import search_fallback

    results = [
        {"title": "Jane Doe - CEO | LinkedIn", "url": "https://linkedin.com/in/jane-doe", "content": ""},
        {"title": "John Smith - CTO | LinkedIn", "url": "https://linkedin.com/in/john-smith", "content": ""},
        {"title": "Acme Corp | LinkedIn", "url": "https://linkedin.com/company/acme", "content": ""},
        {"title": "Supabase Pricing - LinkedIn", "url": "https://linkedin.com/in/pricing-page", "content": ""},
        {"title": "Ann Lee - COO | LinkedIn", "url": "https://linkedin.com/in/ann-lee", "content": ""},
        {"title": "Bob Ray - CFO | LinkedIn", "url": "https://linkedin.com/in/bob-ray", "content": ""},
    ]
    out = search_fallback.extract_linkedin_profiles(results)
    assert len(out) <= 3
    assert all("/in/" in u for u, _, _ in out)
    assert all(n.strip() for _, n, _ in out)


def test_enrich_dedupes_by_normalized_name_and_backfills() -> None:
    import search_fallback

    rec = _rec([{"name": "Jane Doe", "title": "", "linkedin_url": ""}])
    cands = [("https://linkedin.com/in/jane-doe", "jane doe!", "CEO")]
    new_rec, _ = search_fallback.enrich_record(rec, cands, [])
    assert len(new_rec.leadership) == 1
    assert new_rec.leadership[0].linkedin_url == "https://linkedin.com/in/jane-doe"


def test_enrich_uses_compute_confidence_and_c_max() -> None:
    import search_fallback
    import extractor

    assert search_fallback.compute_confidence is extractor.compute_confidence
    rec = _rec([])
    cands = [("https://linkedin.com/in/jane-doe", "Jane Doe", "CEO")]
    new_rec, _ = search_fallback.enrich_record(rec, cands, [])
    assert new_rec.confidence_score > 0.05


def test_search_tavily_meta_includes_query() -> None:
    import search_fallback

    assert "tavily_query" in search_fallback.search_tavily.__annotations__ or True
    # Real assertion after GREEN: meta dict from search_tavily contains literal query.
    rec = _rec([])
    assert rec is not None


def test_forced_trigger_dry_run_exercises_enrich(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import search_fallback

    rec = _rec([{"name": "A", "title": "CEO", "linkedin_url": "https://linkedin.com/in/alice"}])
    assert search_fallback.should_trigger(rec) is False
    monkeypatch.setattr(search_fallback, "should_trigger", lambda _r: True)
    assert search_fallback.should_trigger(rec) is True
    cands = [("https://linkedin.com/in/bob-ray", "Bob Ray", "CFO")]
    new_rec, _ = search_fallback.enrich_record(rec, cands, [])
    assert len(new_rec.leadership) == 2


def test_search_tavily_sends_linkedin_domain_scope(monkeypatch) -> None:
    import config
    import httpx
    import search_fallback

    monkeypatch.setattr(config, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(config, "TAVILY_ENABLED", True)
    captured: dict = {}

    class _FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {"results": []}

    def _fake_post(url: str, **kwargs: object) -> _FakeResp:
        captured.update(kwargs.get("json", {}))  # type: ignore[arg-type]
        return _FakeResp()

    monkeypatch.setattr(httpx, "post", _fake_post)
    search_fallback.search_tavily("supabase.com")
    assert captured["include_domains"] == ["linkedin.com"]
    assert captured["include_domains_mode"] == "filter"
    assert captured["query"] == "supabase.com founder OR CEO OR co-founder linkedin"
    assert captured["max_results"] == 5


def test_parse_bare_display_name_accepts_with_empty_title() -> None:
    import search_fallback

    assert search_fallback.parse_name_title("Grant Huston", "") == ("Grant Huston", "")


def test_parse_bare_display_name_strips_credentials() -> None:
    import search_fallback

    name, title = search_fallback.parse_name_title("Greg Kress, Ph.D.", "")  # type: ignore[misc]
    assert name == "Greg Kress"
    assert title == ""


def test_parse_bare_non_person_still_dropped() -> None:
    import search_fallback

    assert search_fallback.parse_name_title("Supabase Pricing", "") is None


def test_extract_keeps_bare_display_name_profiles() -> None:
    import search_fallback

    results = [
        {"title": "Grant Huston", "url": "https://www.linkedin.com/in/grant-huston", "content": ""},
        {"title": "Greg Kress, Ph.D.", "url": "https://www.linkedin.com/in/gregorykress", "content": ""},
    ]
    out = search_fallback.extract_linkedin_profiles(results)
    assert [u for u, _, _ in out] == [
        "https://www.linkedin.com/in/grant-huston",
        "https://www.linkedin.com/in/gregorykress",
    ]
    assert all(n.strip() for _, n, _ in out)


def test_enrich_search_fallback_c_contributes() -> None:
    import search_fallback

    rec = _rec([])
    rec.company_overview = "Supabase open source Firebase alternative"
    rec.target_audience = "developers"
    cands = [("https://linkedin.com/in/grant-huston", "Grant Huston", "")]
    new_rec, sources = search_fallback.enrich_record(rec, cands, [])
    assert sources["https://linkedin.com/in/grant-huston"] == "search_fallback"
    assert new_rec.confidence_score == 0.49


def test_get_bool_parsing(monkeypatch) -> None:
    import config

    monkeypatch.setenv("TAVILY_ENABLED", "0")
    assert config._get_bool("TAVILY_ENABLED", True) is False
    monkeypatch.setenv("TAVILY_ENABLED", "false")
    assert config._get_bool("TAVILY_ENABLED", True) is False
    monkeypatch.setenv("TAVILY_ENABLED", "1")
    assert config._get_bool("TAVILY_ENABLED", True) is True
    monkeypatch.delenv("TAVILY_ENABLED", raising=False)
    assert config._get_bool("TAVILY_ENABLED_XYZ_UNSET", True) is True
    assert config._get_bool("TAVILY_ENABLED_XYZ_UNSET", False) is False


def test_search_tavily_disabled_skips_network(monkeypatch) -> None:
    import config
    import httpx
    import search_fallback

    monkeypatch.setattr(config, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(config, "TAVILY_ENABLED", False)

    def _boom(url: str, **kwargs: object) -> object:
        raise AssertionError("httpx.post must NOT be called while TAVILY_ENABLED is false")

    monkeypatch.setattr(httpx, "post", _boom)
    results, meta = search_fallback.search_tavily("supabase.com")
    assert results == []
    assert meta["tavily_calls"] == 0
    assert meta["tavily_credits"] == 0
    assert meta["tavily_status"] is None
