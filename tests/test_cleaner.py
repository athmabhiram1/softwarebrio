import re
import config
from cleaner import clean_page, regex_prepass, concat_domain, estimate_tokens


RICH_HTML = """
<html><head><title>Postman Test</title></head><body>
<nav>nav boilerplate links</nav>
<header>header nav</header>
<article>
<h1>Postman is a collaboration platform for API development</h1>
<p>Postman is a collaboration platform for API development. It simplifies every step of building an API and streamlines collaboration.</p>
<p>Developers use Postman to design, test, and document APIs quickly. Teams collaborate across workspaces with version control and CI integration.</p>
<p>Our mission is to make API development accessible to everyone worldwide with powerful tooling and integrations.</p>
<p>Postman supports collections, environments, and automated testing workflows for thousands of teams globally.</p>
<p>Founded in 2014, Postman has grown to serve millions of developers and enterprise customers.</p>
</article>
<script>var x=1;</script>
<style>body{color:red}</style>
<footer>footer links</footer>
</body></html>
"""

JS_SHELL_HTML = "<html><head></head><body><div id='root'></div><script>renderApp();</script><style>.x{}</style></body></html>"

CONTACT_HTML = """
<html><body>
<a href="mailto:Sales@Example.COM">Sales</a>
<a href="mailto:sales@example.com">duplicate sales</a>
<a href="https://linkedin.com/in/john-doe">John</a>
<a href="https://LINKEDIN.COM/company/acme-corp">Acme</a>
<p>Contact us at support@example.com and SUPPORT@example.com for help.</p>
<p>Another email: info@test.org</p>
</body></html>
"""


def test_rich_html_trafilatura_path():
    result = clean_page(RICH_HTML)
    assert result["path"] == "trafilatura", f"expected trafilatura got {result['path']}"
    assert result["clean_len"] < result["raw_len"]
    assert isinstance(result["text"], str) and len(result["text"].strip()) > 100
    print(f"RICH: path={result['path']} raw={result['raw_len']} clean={result['clean_len']}")


def test_js_shell_bs4_fallback():
    result = clean_page(JS_SHELL_HTML)
    assert result["path"] == "bs4"
    assert isinstance(result["text"], str)
    # empty shell should be empty or very short but still string
    print(f"JS_SHELL: path={result['path']} text={repr(result['text'][:100])}")


def test_mailto_linkedin_prepass_deduped():
    result = regex_prepass(CONTACT_HTML)
    # linkedin deduped exact-match, case-sensitive
    li = result["linkedin_urls"]
    assert len(li) >= 2, f"expected >=2 linkedin got {li}"
    # check both patterns present (case-insensitive)
    assert any("linkedin.com/in/john-doe" in u.lower() for u in li)
    assert any("linkedin.com/company/acme-corp" in u.lower() for u in li)
    # emails lowercased, deduped case-insensitively, preserve order
    emails = result["emails"]
    assert emails == [e.lower() for e in emails], "emails not lowercased"
    assert len(emails) == len(set(e.lower() for e in emails)), "emails not deduped"
    assert "sales@example.com" in emails
    assert "support@example.com" in emails
    assert emails.count("sales@example.com") == 1
    assert emails.count("support@example.com") == 1
    # first-seen order: sales before support
    assert emails.index("sales@example.com") < emails.index("support@example.com")
    print(f"PREPASS linkedin={li} emails={emails}")


def test_over_budget_multi_page_trimming():
    # create over-budget pages: team small, pricing huge
    team_text = "team content word " * 800  # ~ 14400 chars? actually 17*800=13600
    pricing_text = "pricing content word " * 4000  # ~76000 chars
    # wrap in rich article to ensure trafilatura extracts
    def wrap(t):
        return f"<html><body><article><p>{t}</p></article></body></html>"
    pages = {
        "https://example.com/team": wrap(team_text),
        "https://example.com/pricing": wrap(pricing_text),
    }
    # also include homepage small for priority check
    result = concat_domain(pages)
    total_tokens = estimate_tokens(result["text"])
    assert total_tokens <= config.TOKEN_BUDGET, f"over budget {total_tokens} > {config.TOKEN_BUDGET}"
    # team intact: should contain team content fully (original 800 repeats)
    # pricing trimmed: should be shorter than original
    team_clean = clean_page(pages["https://example.com/team"])["text"]
    pricing_clean = clean_page(pages["https://example.com/pricing"])["text"]
    # team text should be intact inside result
    assert team_clean.strip() in result["text"], "team text not intact"
    # pricing should be trimmed (if over budget, pricing is lowest priority)
    # result text contains pricing but shorter than original
    assert len(result["text"]) < len(team_clean) + len(pricing_clean)
    # ensure pricing still present but not full length (if budget exceeded)
    # Check that pricing content is present but truncated
    if len(pricing_clean) > 1000:
        assert pricing_clean[:500] in result["text"], "pricing start should remain"
        # full pricing should not be intact if trimming happened
        assert pricing_clean not in result["text"], "pricing should be trimmed not intact"
    print(f"CONCAT tokens={total_tokens} budget={config.TOKEN_BUDGET} team_len={len(team_clean)} pricing_orig={len(pricing_clean)} result_len={len(result['text'])}")
    print(f"found_linkedin {result['found_linkedin_urls']} found_emails {result['found_emails']}")
