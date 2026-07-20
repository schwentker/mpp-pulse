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
    report = app.fallback_summary("2026-07-18", 7, [item])
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
    assert app.is_relevant_payment_news("x.402 support announced")
    assert app.is_relevant_payment_news("paymentauth.org published draft-httpauth-payment-00")


def test_news_relevance_accepts_watched_people_only_with_organization():
    assert app.is_relevant_payment_news("Jake Moxey at Tempo Labs updated the draft")
    assert app.is_relevant_payment_news("Steve Kaliski from Stripe shared a protocol update")
    assert not app.is_relevant_payment_news("A different Brendan Ryan released a photography book")


def test_fallback_describes_configured_window():
    report = app.fallback_summary("2026-07-19", 7, [])
    assert "last 7 days" in report
    assert "last 24 hours" not in report


def test_daily_fallback_describes_one_day_window():
    report = app.fallback_summary("2026-07-19", 1, [])
    assert "last 1 day" in report


def test_run_window_defaults_collection_and_report_separately(monkeypatch):
    monkeypatch.setenv("COLLECTION_WINDOW_DAYS", "2")
    monkeypatch.setenv("REPORT_WINDOW_DAYS", "7")
    started = datetime(2026, 7, 19, 12, tzinfo=UTC)
    collect = app.resolve_run_window({"mode": "collect"}, started)
    report = app.resolve_run_window({"mode": "report"}, started)
    assert collect[1] == 2
    assert report[1] == 7
    assert collect[2].isoformat() == "2026-07-19"


def test_run_lock_key_is_bound_to_mode_window_and_period_end():
    end = datetime(2026, 7, 19, tzinfo=UTC).date()
    assert app.run_lock_key("collect", end, 1) == "LOCK#COLLECT#END#2026-07-19#WINDOW#1"
    assert app.run_lock_key("report", end, 7) == "LOCK#REPORT#END#2026-07-19#WINDOW#7"


def test_period_label_is_not_hardcoded_to_weekly():
    assert app.period_label(1) == "Daily"
    assert app.period_label(7) == "Weekly"
    assert app.period_label(3) == "3-Day"


def test_evidence_row_contains_review_fields_and_stable_identity():
    item = {
        "source": "github",
        "external_id": "repo:sha",
        "title": "MPP release",
        "url": "https://example.com/mpp",
        "published_at": "2026-07-19T08:00:00Z",
        "summary": "MPP payment protocol release",
        "category": "code",
        "news_eligible": True,
    }
    row = app.evidence_row(item, "run-1", "2026-07-19T09:00:00Z")
    assert row[0] == app.content_id(item)
    assert row[2] == "2026-07-19T09:00:00Z"
    assert row[7] == "https://example.com/mpp"
    assert row[12] == "news"
    assert row[13] == "PENDING"


def test_sheet_upsert_preserves_operator_fields_and_is_idempotent(monkeypatch):
    item = {
        "source": "github",
        "external_id": "repo:sha",
        "title": "MPP release",
        "url": "https://example.com/mpp",
        "published_at": "2026-07-19T08:00:00Z",
        "summary": "MPP payment protocol release",
        "category": "code",
        "news_eligible": True,
    }
    row = app.evidence_row(item, "run-1", "2026-07-19T09:00:00Z")
    stored_rows = []

    def fake_request(method, path, token, payload=None):
        if method == "GET" and "A1%3AO1" in path:
            return {"values": [list(app.GOOGLE_SHEETS_HEADERS)]}
        if method == "GET":
            return {"values": stored_rows}
        if method == "POST":
            stored_rows.append(list(payload["values"][0]))
            return {}
        if method == "PUT":
            stored_rows[0] = list(payload["values"][0])
            return {}
        raise AssertionError(method)

    monkeypatch.setattr(app, "google_sheets_request", fake_request)
    assert app.upsert_evidence_rows("token", "sheet", [row]) == {"updated": 0, "appended": 1}
    stored_rows[0][13] = "KEEP"
    stored_rows[0][14] = "reviewed"
    assert app.upsert_evidence_rows("token", "sheet", [row]) == {"updated": 1, "appended": 0}
    assert stored_rows[0][13:15] == ["KEEP", "reviewed"]


def test_google_sheets_integration_is_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_ENABLED", raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PARAMETER", raising=False)
    result = app.sync_evidence_to_sheet([], "run-1", "2026-07-19T09:00:00Z")
    assert result["status"] == "disabled"
    assert result["rows"] == 0


def test_draft_loader_uses_only_kept_news_inside_the_reporting_window(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PARAMETER", "parameter")
    item = {
        "source": "github",
        "external_id": "repo:keep",
        "title": "MPP release",
        "url": "https://example.com/keep",
        "published_at": "2026-07-19T08:00:00Z",
        "summary": "MPP payment protocol release",
        "category": "code",
        "news_eligible": True,
    }
    keep = app.evidence_row(item, "run-1", "2026-07-19T09:00:00Z")
    keep[13] = "KEEP"
    cut = list(keep)
    cut[0] = "cut-item"
    cut[13] = "CUT"
    inventory = list(keep)
    inventory[0] = "inventory-item"
    inventory[12] = "inventory"
    old = list(keep)
    old[0] = "old-item"
    old[3] = "2026-07-01T08:00:00Z"
    monkeypatch.setattr(app, "get_google_access_token", lambda: "token")
    monkeypatch.setattr(
        app,
        "google_sheets_request",
        lambda method, path, token, payload=None: {"values": [keep, cut, inventory, old]},
    )

    result = app.load_kept_sheet_items(
        datetime(2026, 7, 13, tzinfo=UTC), datetime(2026, 7, 20, tzinfo=UTC)
    )

    assert result["status"] == "ready"
    assert result["kept_rows"] == 3
    assert result["eligible_kept_rows"] == 1
    assert [item["item_id"] for item in result["items"]] == [keep[0]]


def test_draft_mode_generates_operator_review_artifacts_only(monkeypatch):
    started = datetime(2026, 7, 19, 12, tzinfo=UTC)
    item = {
        "item_id": "item-1",
        "source": "github",
        "title": "MPP release",
        "url": "https://example.com/mpp",
        "published_at": "2026-07-19T08:00:00Z",
        "summary": "release",
        "importance_score": 90,
        "review_status": "KEEP",
    }
    monkeypatch.setenv("BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(app, "now_utc", lambda: started)
    monkeypatch.setattr(
        app,
        "load_kept_sheet_items",
        lambda start, end: {"status": "ready", "items": [item], "kept_rows": 1, "eligible_kept_rows": 1},
    )
    monkeypatch.setattr(app, "summarize", lambda date, days, items: "Pulse signal: tested")
    monkeypatch.setattr(app, "render_html", lambda date, days, summary, run_id: "<html></html>")
    monkeypatch.setattr(app, "write_curation_snapshot", lambda *args: "drafts/snapshot.json")
    monkeypatch.setattr(app, "send_draft_review_email", lambda *args: {"status": "disabled"})
    report_email = MagicMock(side_effect=AssertionError("subscriber report email must not run"))
    monkeypatch.setattr(app, "send_report_email", report_email)
    monkeypatch.setattr(app.S3, "generate_presigned_url", lambda *args, **kwargs: "https://example.com/draft")

    result = app.lambda_handler({"mode": "draft", "force": True, "window_days": 7}, None)

    assert result["status"] == "SUCCEEDED"
    assert result["draft"]["status"] == "generated"
    assert result["report_key"].startswith("drafts/2026-07-19/")
    assert result["snapshot_key"] == "drafts/snapshot.json"
    report_email.assert_not_called()


def test_draft_mode_fails_closed_without_kept_evidence(monkeypatch):
    started = datetime(2026, 7, 19, 12, tzinfo=UTC)
    monkeypatch.setattr(app, "now_utc", lambda: started)
    monkeypatch.setattr(
        app,
        "load_kept_sheet_items",
        lambda start, end: {"status": "ready", "items": [], "kept_rows": 0, "eligible_kept_rows": 0},
    )
    draft_email = MagicMock(side_effect=AssertionError("draft email must not run"))
    monkeypatch.setattr(app, "send_draft_review_email", draft_email)

    result = app.lambda_handler({"mode": "draft", "force": True, "window_days": 7}, None)

    assert result["status"] == "NO_APPROVED_EVIDENCE"
    assert result["draft"]["status"] == "no_approved_evidence"
    draft_email.assert_not_called()


def test_collect_mode_persists_evidence_without_generating_a_report(monkeypatch):
    started = datetime(2026, 7, 19, 12, tzinfo=UTC)
    monkeypatch.setattr(app, "now_utc", lambda: started)
    monkeypatch.setattr(app, "collect_mpp", lambda: [])
    monkeypatch.setattr(app, "collect_tempo", lambda since: [])
    monkeypatch.setattr(app, "collect_github", lambda since: [])
    monkeypatch.setattr(app, "collect_hacker_news", lambda since: [])
    monkeypatch.setattr(app, "collect_reddit", lambda since: [])
    monkeypatch.setattr(app, "collect_x", lambda since: [])
    monkeypatch.setattr(app, "persist_new_items", lambda items, seen_at: [])
    report_writer = MagicMock()
    monkeypatch.setattr(app.S3, "put_object", report_writer)

    result = app.lambda_handler({"mode": "collect", "force": True, "window_days": 1}, None)

    assert result["status"] == "SUCCEEDED"
    assert result["mode"] == "collect"
    assert result["collection_since"] == "2026-07-18T12:00:00Z"
    assert "report_key" not in result
    report_writer.assert_not_called()


def test_report_mode_reads_persisted_evidence_without_collecting(monkeypatch):
    started = datetime(2026, 7, 19, 12, tzinfo=UTC)
    monkeypatch.setenv("BUCKET_NAME", "test-bucket")
    item = {
        "item_id": "item-1",
        "title": "MPP release",
        "url": "https://example.com/mpp",
        "summary": "release",
        "source": "github",
        "importance_score": 90,
    }
    monkeypatch.setattr(app, "now_utc", lambda: started)
    monkeypatch.setattr(app, "load_report_items", lambda start, end: [item])
    monkeypatch.setattr(app, "summarize", lambda date, days, items: "Pulse signal: tested")
    monkeypatch.setattr(app, "render_html", lambda date, days, summary, run_id: "<html></html>")
    monkeypatch.setattr(app, "send_report_email", lambda *args: {"status": "disabled"})
    monkeypatch.setattr(app, "collect_mpp", MagicMock(side_effect=AssertionError("should not collect")))
    monkeypatch.setattr(app.S3, "generate_presigned_url", lambda *args, **kwargs: "https://example.com/report")

    result = app.lambda_handler({"mode": "report", "force": True, "window_days": 7}, None)

    assert result["status"] == "SUCCEEDED"
    assert result["mode"] == "report"
    assert result["news_items"] == 1
    assert result["report_key"].startswith("reports/2026-07-19/")


def test_tempo_publication_date_extraction():
    page = '<meta property="article:published_time" content="2026-07-15T08:30:00Z">'
    assert app.extract_published_at(page) == datetime(2026, 7, 15, 8, 30, tzinfo=UTC)
