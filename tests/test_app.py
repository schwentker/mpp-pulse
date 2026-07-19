import importlib.util
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock


sys.modules["boto3"] = MagicMock()
botocore = MagicMock()
sys.modules["botocore"] = botocore
sys.modules["botocore.exceptions"] = botocore.exceptions
os.environ.setdefault("TABLE_NAME", "test-table")

APP_PATH = Path(__file__).parents[1] / "src" / "app.py"
SPEC = importlib.util.spec_from_file_location("app", APP_PATH)
app = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(app)


def test_normalize_url_removes_tracking_and_fragment():
    value = app.normalize_url("HTTPS://Example.com/a/?utm_source=x&id=4#top")
    assert value == "https://example.com/a?id=4"


def test_score_prioritizes_official_protocol_signal():
    record = {
        "source": "mpp_catalog",
        "title": "MPP protocol SDK release",
        "summary": "New payment settlement integration",
    }
    assert app.score(record) >= 75


def test_fallback_report_contains_source_labels():
    item = {
        "title": "MPP service: Example",
        "url": "https://example.com",
        "importance_score": 80,
    }
    report = app.fallback_summary("2026-07-18", [item])
    assert "[S1]" in report
    assert "https://example.com" in report


def test_github_since_time_is_utc_serializable():
    assert app.iso(datetime(2026, 7, 18, tzinfo=UTC)) == "2026-07-18T00:00:00Z"


def test_content_id_ignores_collection_timestamp():
    first = {
        "source": "mpp_catalog",
        "external_id": "example",
        "title": "MPP service: Example",
        "url": "https://example.com/",
        "summary": "stable content",
        "category": "service",
        "published_at": "2026-07-18T01:00:00Z",
    }
    second = {**first, "published_at": "2026-07-18T02:00:00Z"}
    assert app.content_id(first) == app.content_id(second)


def test_markdown_report_renders_headings_bold_and_links():
    rendered = app.markdown_to_html("## Signal\n**MPP** — https://mpp.dev")
    assert "<h2>Signal</h2>" in rendered
    assert "<strong>MPP</strong>" in rendered
    assert 'href="https://mpp.dev"' in rendered


def test_news_relevance_rejects_unrelated_hacker_news_titles():
    unrelated = (
        "Show HN: Eremite, an offline-first data layer that can talk to any back end "
        "with temporary local storage"
    )
    assert not app.is_relevant_payment_news(unrelated)


def test_news_relevance_accepts_explicit_machine_payment_terms():
    assert app.is_relevant_payment_news("MPP adds another HTTP 402 payment method")
    assert app.is_relevant_payment_news("Tempo launches stablecoin settlement for agents")
