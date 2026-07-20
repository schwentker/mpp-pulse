from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from xml.etree import ElementTree

import boto3
from botocore.exceptions import ClientError


USER_AGENT = "mpp-pulse/0.1 (+https://github.com/OWNER/mpp-pulse)"
MPP_CATALOG_URL = "https://mpp.dev/api/services"
TEMPO_BLOG_URL = "https://tempo.xyz/blog"
DEFAULT_GITHUB_REPO = "tempoxyz/mpp"
PAYMENTAUTH_GITHUB_REPO = "tempoxyz/mpp-specs"
REDDIT_SEARCH_URL = "https://www.reddit.com/search.rss"
HACKER_NEWS_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
X_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
IETF_PAYMENT_DRAFT_URL = "https://datatracker.ietf.org/doc/draft-ryan-httpauth-payment/"
OFFICIAL_BLOG_HOSTS = (
    "tempo.xyz", "coinbase.com", "cdp.coinbase.com", "x402.org",
    "blog.cloudflare.com", "circle.com", "paymentauth.org",
)
NEWS_QUERIES = (
    "MPP",
    '"machine payments protocol"',
    "x402",
    '"x.402"',
    '"HTTP 402"',
    "paymentauth.org",
    '"draft-httpauth-payment-00"',
    '"draft-ryan-httpauth-payment"',
    '"Brendan Ryan" "Tempo Labs"',
    '"Jake Moxey" "Tempo Labs"',
    '"Tom Meagher" "Tempo Labs"',
    '"Jeff Weinstein" Stripe',
    '"Steve Kaliski" Stripe',
)
WATCHED_PEOPLE = (
    ("brendan ryan", "tempo"),
    ("jake moxey", "tempo"),
    ("tom meagher", "tempo"),
    ("jeff weinstein", "stripe"),
    ("steve kaliski", "stripe"),
)
DEFAULT_COLLECTION_WINDOW_DAYS = 1
DEFAULT_REPORT_WINDOW_DAYS = 7
MAX_REPORT_ITEMS = 50
RUN_MODES = {"collect", "draft", "report"}
COLLECTOR_REGISTRY_FIELDS = (
    "id",
    "source_type",
    "collection",
    "entity_match_rule",
    "tier",
    "heartbeat_periods",
    "eligibility",
)
# This registry is the source-contract layer for the incremental collector
# refactor. Only mpp_catalog is migrated in Stage 5; the remaining sources keep
# their existing execution paths until parity tests cover the bulk migration.
COLLECTOR_REGISTRY = {
    "mpp_catalog": {
        "id": "mpp_catalog",
        "source_type": "catalog_snapshot",
        "collection": {"method": "GET", "url_pattern": MPP_CATALOG_URL, "auth": "none"},
        "entity_match_rule": "service id, slug, or name",
        "tier": 1,
        "heartbeat_periods": 1,
        "eligibility": "inventory",
        "implementation_status": "migrated",
    },
    "tempo_blog": {
        "id": "tempo_blog",
        "source_type": "ecosystem",
        "collection": {"method": "GET", "url_pattern": TEMPO_BLOG_URL, "auth": "none"},
        "entity_match_rule": "official Tempo article URL",
        "tier": 1,
        "heartbeat_periods": 7,
        "eligibility": "news",
        "implementation_status": "planned",
    },
    "github": {
        "id": "github",
        "source_type": "code",
        "collection": {"method": "GET", "url_pattern": "https://api.github.com/repos/{repo}/commits", "auth": "optional_bearer"},
        "entity_match_rule": "repository and commit SHA",
        "tier": 1,
        "heartbeat_periods": 1,
        "eligibility": "news",
        "implementation_status": "planned",
    },
    "hacker_news": {
        "id": "hacker_news",
        "source_type": "community",
        "collection": {"method": "GET", "url_pattern": HACKER_NEWS_SEARCH_URL, "auth": "none"},
        "entity_match_rule": "Hacker News object ID or canonical URL",
        "tier": 3,
        "heartbeat_periods": 1,
        "eligibility": "news",
        "implementation_status": "planned",
    },
    "reddit": {
        "id": "reddit",
        "source_type": "community",
        "collection": {"method": "GET", "url_pattern": REDDIT_SEARCH_URL, "auth": "none"},
        "entity_match_rule": "canonical Reddit post URL",
        "tier": 3,
        "heartbeat_periods": 1,
        "eligibility": "news",
        "implementation_status": "planned",
    },
    "x": {
        "id": "x",
        "source_type": "community",
        "collection": {"method": "GET", "url_pattern": X_SEARCH_URL, "auth": "optional_bearer"},
        "entity_match_rule": "X post ID",
        "tier": 3,
        "heartbeat_periods": 1,
        "eligibility": "news",
        "implementation_status": "planned",
    },
    "ietf_payment_auth": {
        "id": "ietf_payment_auth", "source_type": "specification",
        "collection": {"method": "GET", "url_pattern": IETF_PAYMENT_DRAFT_URL, "auth": "none"},
        "entity_match_rule": "draft-ryan-httpauth-payment revision", "tier": 1,
        "heartbeat_periods": 7, "eligibility": "news", "implementation_status": "migrated",
    },
    "github_release": {
        "id": "github_release", "source_type": "release",
        "collection": {"method": "GET", "url_pattern": "https://github.com/{repo}/releases.atom", "auth": "none"},
        "entity_match_rule": "allowlisted repository and release tag", "tier": 1,
        "heartbeat_periods": 7, "eligibility": "news", "implementation_status": "migrated",
    },
    "github_tag": {
        "id": "github_tag", "source_type": "implementation_activity",
        "collection": {"method": "GET", "url_pattern": "https://github.com/{repo}/tags.atom", "auth": "none"},
        "entity_match_rule": "allowlisted repository and tag", "tier": 1,
        "heartbeat_periods": 7, "eligibility": "news", "implementation_status": "migrated",
    },
    "github_merged_pr": {
        "id": "github_merged_pr", "source_type": "implementation_activity",
        "collection": {"method": "GET", "url_pattern": "https://api.github.com/repos/{repo}/pulls", "auth": "optional_bearer"},
        "entity_match_rule": "allowlisted repository and merged pull request", "tier": 1,
        "heartbeat_periods": 7, "eligibility": "news", "implementation_status": "migrated",
    },
    "official_blog": {
        "id": "official_blog", "source_type": "official_blog",
        "collection": {"method": "GET", "url_pattern": "configured RSS feeds", "auth": "none"},
        "entity_match_rule": "allowlisted host and explicit protocol entity", "tier": 1,
        "heartbeat_periods": 7, "eligibility": "news", "implementation_status": "migrated",
    },
}

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
S3 = boto3.client("s3")
BEDROCK = boto3.client("bedrock-runtime")
SES = boto3.client("ses")
SSM = boto3.client("ssm")

GOOGLE_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
GOOGLE_SHEETS_TAB = "evidence_inbox"
GOOGLE_SHEETS_HEADERS = (
    "item_id",
    "run_id",
    "collected_at",
    "published_at",
    "source",
    "source_type",
    "title",
    "canonical_url",
    "supporting_excerpt",
    "entity_match",
    "confidence",
    "importance_score",
    "eligibility",
    "review_status",
    "operator_note",
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def default_window_days(mode: str) -> int:
    if mode == "collect":
        return positive_int(
            os.getenv("COLLECTION_WINDOW_DAYS"), DEFAULT_COLLECTION_WINDOW_DAYS
        )
    return positive_int(os.getenv("REPORT_WINDOW_DAYS"), DEFAULT_REPORT_WINDOW_DAYS)


def parse_window_end(value: Any, fallback: datetime) -> date:
    if value:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            raise ValueError("window_end or report_date must use YYYY-MM-DD") from None
    return fallback.date()


def resolve_run_window(
    event: dict[str, Any], started: datetime
) -> tuple[str, int, date, datetime, datetime]:
    mode = str(event.get("mode") or "report").lower()
    if mode not in RUN_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    window_days = positive_int(event.get("window_days"), default_window_days(mode))
    window_end = parse_window_end(event.get("window_end") or event.get("report_date"), started)
    window_start = datetime.combine(window_end - timedelta(days=window_days - 1), time.min, UTC)
    window_end_exclusive = datetime.combine(window_end + timedelta(days=1), time.min, UTC)
    return mode, window_days, window_end, window_start, window_end_exclusive


def run_lock_key(mode: str, window_end: date, window_days: int) -> str:
    return f"LOCK#{mode.upper()}#END#{window_end.isoformat()}#WINDOW#{window_days}"


def period_label(window_days: int) -> str:
    if window_days == 1:
        return "Daily"
    if window_days == 7:
        return "Weekly"
    return f"{window_days}-Day"


def google_sheets_enabled() -> bool:
    return (
        os.getenv("GOOGLE_SHEETS_ENABLED", "false").strip().lower() == "true"
        and bool(os.getenv("GOOGLE_SHEET_ID", "").strip())
        and bool(os.getenv("GOOGLE_CREDENTIALS_PARAMETER", "").strip())
    )


def google_sheets_config_status() -> dict[str, Any]:
    if os.getenv("GOOGLE_SHEETS_ENABLED", "false").strip().lower() != "true":
        return {"status": "disabled", "reason": "GOOGLE_SHEETS_ENABLED is not true"}
    if not os.getenv("GOOGLE_SHEET_ID", "").strip():
        return {"status": "disabled", "reason": "GOOGLE_SHEET_ID is not configured"}
    if not os.getenv("GOOGLE_CREDENTIALS_PARAMETER", "").strip():
        return {
            "status": "disabled",
            "reason": "GOOGLE_CREDENTIALS_PARAMETER is not configured",
        }
    return {"status": "configured"}


def get_google_access_token() -> str:
    parameter_name = os.environ["GOOGLE_CREDENTIALS_PARAMETER"]
    response = SSM.get_parameter(Name=parameter_name, WithDecryption=True)
    raw_credentials = response["Parameter"]["Value"]
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError("google-auth is required for Google Sheets integration") from exc
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(raw_credentials),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    credentials.refresh(Request())
    if not credentials.token:
        raise RuntimeError("Google service account did not return an access token")
    return credentials.token


def google_sheets_request(
    method: str,
    path: str,
    access_token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{GOOGLE_SHEETS_API}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Sheets API returned {exc.code}: {detail}") from exc
    if not response_body:
        return {}
    return json.loads(response_body)


def sheet_range_path(sheet_id: str, cell_range: str) -> str:
    encoded_range = urllib.parse.quote(cell_range, safe="")
    return f"/{urllib.parse.quote(sheet_id, safe='')}/values/{encoded_range}"


def evidence_row(item: dict[str, Any], run_id: str, collected_at: str) -> list[Any]:
    searchable = f"{item.get('title', '')} {item.get('summary', '')}"
    matched = is_relevant_payment_news(searchable)
    category = str(item.get("category") or "")
    return [
        content_id(item),
        run_id,
        collected_at,
        str(item.get("published_at") or ""),
        str(item.get("source") or ""),
        category,
        str(item.get("title") or "")[:500],
        str(item.get("url") or ""),
        str(item.get("summary") or "")[:1500],
        "matched relevance predicate" if matched else "",
        "medium" if matched else "low",
        score(item),
        "news" if item.get("news_eligible", False) else "inventory",
        "PENDING",
        "",
    ]


def upsert_evidence_rows(
    access_token: str,
    sheet_id: str,
    rows: list[list[Any]],
    tab_name: str = GOOGLE_SHEETS_TAB,
) -> dict[str, int]:
    header_range = sheet_range_path(sheet_id, f"{tab_name}!A1:O1")
    header_response = google_sheets_request("GET", header_range, access_token)
    if not header_response.get("values"):
        google_sheets_request(
            "PUT",
            f"{header_range}?valueInputOption=RAW",
            access_token,
            {"range": f"{tab_name}!A1:O1", "majorDimension": "ROWS", "values": [list(GOOGLE_SHEETS_HEADERS)]},
        )

    values_range = sheet_range_path(sheet_id, f"{tab_name}!A2:O")
    existing_values = google_sheets_request("GET", values_range, access_token).get("values", [])
    existing_rows = {
        str(row[0]): (index + 2, row)
        for index, row in enumerate(existing_values)
        if row and row[0]
    }
    updated = 0
    appended = 0
    for row in rows:
        item_id = str(row[0])
        existing = existing_rows.get(item_id)
        if existing:
            row_number, existing_row = existing
            if len(existing_row) >= 15:
                row[13] = existing_row[13]
                row[14] = existing_row[14]
            update_range = sheet_range_path(sheet_id, f"{tab_name}!A{row_number}:O{row_number}")
            google_sheets_request(
                "PUT",
                f"{update_range}?valueInputOption=RAW",
                access_token,
                {"range": f"{tab_name}!A{row_number}:O{row_number}", "majorDimension": "ROWS", "values": [row]},
            )
            updated += 1
        else:
            append_range = sheet_range_path(sheet_id, f"{tab_name}!A:O")
            google_sheets_request(
                "POST",
                f"{append_range}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
                access_token,
                {"majorDimension": "ROWS", "values": [row]},
            )
            appended += 1
    return {"updated": updated, "appended": appended}


def sync_evidence_to_sheet(
    items: list[dict[str, Any]], run_id: str, collected_at: str
) -> dict[str, Any]:
    status = google_sheets_config_status()
    if status["status"] != "configured":
        return {**status, "rows": 0}
    try:
        access_token = get_google_access_token()
        rows = [evidence_row(item, run_id, collected_at) for item in items]
        counts = upsert_evidence_rows(
            access_token,
            os.environ["GOOGLE_SHEET_ID"].strip(),
            rows,
        )
        return {"status": "synced", "rows": len(rows), **counts}
    except Exception as exc:
        print(json.dumps({"level": "ERROR", "component": "google_sheets", "error": str(exc)[:500]}))
        return {"status": "failed", "rows": len(items), "error": f"{type(exc).__name__}: {exc}"[:500]}


def sheet_row_values(row: list[Any]) -> dict[str, str]:
    return {
        header: str(row[index]).strip() if index < len(row) else ""
        for index, header in enumerate(GOOGLE_SHEETS_HEADERS)
    }


def sheet_row_to_item(row: list[Any]) -> dict[str, Any] | None:
    values = sheet_row_values(row)
    if not all(
        (
            values["item_id"],
            values["source"],
            values["title"],
            values["canonical_url"],
            values["published_at"],
        )
    ):
        return None
    try:
        importance_score = int(values["importance_score"])
    except ValueError:
        importance_score = 0
    return {
        "item_id": values["item_id"],
        "source": values["source"],
        "external_id": values["item_id"],
        "title": values["title"],
        "url": values["canonical_url"],
        "published_at": values["published_at"],
        "summary": values["supporting_excerpt"],
        "category": values["source_type"],
        "news_eligible": values["eligibility"].lower() == "news",
        "importance_score": importance_score,
        "review_status": values["review_status"].upper(),
    }


def load_kept_sheet_items(
    window_start: datetime, window_end_exclusive: datetime
) -> dict[str, Any]:
    config_status = google_sheets_config_status()
    if config_status["status"] != "configured":
        return {"status": "not_ready", "reason": config_status["reason"], "items": []}
    try:
        access_token = get_google_access_token()
        sheet_id = os.environ["GOOGLE_SHEET_ID"].strip()
        values_range = sheet_range_path(sheet_id, f"{GOOGLE_SHEETS_TAB}!A2:O")
        values = google_sheets_request("GET", values_range, access_token).get("values", [])
        kept_rows = 0
        items: list[dict[str, Any]] = []
        for row in values:
            item = sheet_row_to_item(row)
            if not item or item["review_status"] != "KEEP":
                continue
            kept_rows += 1
            published = parse_date(item["published_at"])
            if not published or not window_start <= published < window_end_exclusive:
                continue
            if not item["news_eligible"]:
                continue
            items.append(item)
        deduped = {item["item_id"]: item for item in items}
        report_items = list(deduped.values())
        report_items.sort(key=lambda item: item["importance_score"], reverse=True)
        return {
            "status": "ready",
            "items": report_items,
            "kept_rows": kept_rows,
            "eligible_kept_rows": len(report_items),
        }
    except Exception as exc:
        print(json.dumps({"level": "ERROR", "component": "google_sheets", "error": str(exc)[:500]}))
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500], "items": []}


def fetch(url: str, *, token: str | None = None) -> tuple[bytes, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/html,*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read(), response.headers.get("content-type", "")


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def content_id(item: dict[str, Any]) -> str:
    material = {
        key: item.get(key)
        for key in ("source", "external_id", "title", "url", "summary", "category")
    }
    return stable_id(
        item["source"],
        item["external_id"],
        json.dumps(material, sort_keys=True, separators=(",", ":")),
    )


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query)
        if not key.lower().startswith("utm_") and key.lower() not in {"ref", "source"}
    ]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            urllib.parse.urlencode(query),
            "",
        )
    )


def collector_definition(collector_id: str) -> dict[str, Any]:
    definition = COLLECTOR_REGISTRY.get(collector_id)
    if definition is None:
        raise ValueError(f"unknown collector: {collector_id}")
    missing = [field for field in COLLECTOR_REGISTRY_FIELDS if field not in definition]
    if missing:
        raise ValueError(f"collector {collector_id} is missing fields: {', '.join(missing)}")
    return definition


def parse_mpp_catalog(payload: Any, collected_at: datetime) -> list[dict[str, Any]]:
    """Turn the MPP catalog response into inventory records without I/O."""
    if isinstance(payload, dict):
        for key in ("services", "items", "data", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError("MPP catalog returned an unsupported JSON shape")

    items = []
    for service in payload[:100]:
        if not isinstance(service, dict):
            continue
        service_key = str(service.get("id") or service.get("slug") or service.get("name") or "")
        if not service_key:
            continue
        items.append(
            {
                "source": "mpp_catalog",
                "external_id": service_key,
                "title": f"MPP service: {service.get('name') or service_key}",
                "url": normalize_url(
                    str(service.get("url") or service.get("homepage") or collector_definition("mpp_catalog")["collection"]["url_pattern"])
                ),
                "published_at": iso(collected_at),
                "summary": json.dumps(service, sort_keys=True, default=str)[:2500],
                "category": collector_definition("mpp_catalog")["source_type"],
                "news_eligible": collector_definition("mpp_catalog")["eligibility"] == "news",
            }
        )
    return items


def collect_mpp() -> list[dict[str, Any]]:
    definition = collector_definition("mpp_catalog")
    body, _ = fetch(definition["collection"]["url_pattern"])
    return parse_mpp_catalog(json.loads(body), now_utc())


def extract_published_at(page: str) -> datetime | None:
    patterns = (
        r"""property=["']article:published_time["'][^>]+content=["']([^"']+)""",
        r"""content=["']([^"']+)["'][^>]+property=["']article:published_time["']""",
        r'''"datePublished"\s*:\s*"([^"]+)"''',
        r"""<time[^>]+datetime=["']([^"']+)""",
    )
    for pattern in patterns:
        match = re.search(pattern, page, re.IGNORECASE)
        if match:
            published = parse_date(html.unescape(match.group(1)))
            if published:
                return published
    return None


def collect_tempo(since: datetime) -> list[dict[str, Any]]:
    body, _ = fetch(TEMPO_BLOG_URL)
    page = body.decode("utf-8", errors="replace")
    links: dict[str, str] = {}
    pattern = re.compile(
        r"""<a[^>]+href=["']([^"']+)["'][^>]*>(.*?)</a>""", re.IGNORECASE | re.DOTALL
    )
    for href, label in pattern.findall(page):
        url = normalize_url(urllib.parse.urljoin(TEMPO_BLOG_URL, href))
        if not url.startswith("https://tempo.xyz/blog/"):
            continue
        title = html.unescape(re.sub(r"<[^>]+>", " ", label))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            links[url] = title

    items = []
    for url, title in list(links.items())[:20]:
        article_body, _ = fetch(url)
        article_page = article_body.decode("utf-8", errors="replace")
        published = extract_published_at(article_page)
        if not published or published < since:
            continue
        items.append(
            {
                "source": "tempo_blog",
                "external_id": url,
                "title": title,
                "url": url,
                "published_at": iso(published),
                "summary": "Official Tempo blog post published within the report window.",
                "category": "ecosystem",
                "news_eligible": True,
            }
        )
    return items


def collect_github(since: datetime) -> list[dict[str, Any]]:
    items = []
    configured_repo = os.getenv("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPO)
    repos = dict.fromkeys((configured_repo, PAYMENTAUTH_GITHUB_REPO))
    params = urllib.parse.urlencode({"since": iso(since), "per_page": "100"})
    token = os.getenv("GITHUB_TOKEN") or None
    for repo in repos:
        body, _ = fetch(f"https://api.github.com/repos/{repo}/commits?{params}", token=token)
        payload = json.loads(body)
        if not isinstance(payload, list):
            raise ValueError(f"GitHub commits endpoint returned a non-list response for {repo}")
        for record in payload:
            commit = record.get("commit") or {}
            author = commit.get("author") or {}
            message = str(commit.get("message") or "")
            sha = str(record.get("sha") or "")
            items.append(
                {
                    "source": "github",
                    "external_id": f"{repo}:{sha}",
                    "title": f"{repo}: {message.splitlines()[0][:180]}",
                    "url": normalize_url(
                        str(record.get("html_url") or f"https://github.com/{repo}")
                    ),
                    "published_at": str(author.get("date") or iso(now_utc())),
                    "summary": message[:2500],
                    "category": "code",
                    "news_eligible": True,
                }
            )
    return items


def has_explicit_tier_one_evidence(text: str) -> bool:
    value = text.lower()
    return any(term in value for term in (
        "mpp", "machine payments protocol", "x402", "paymentauth", "payment auth",
        "http 402", "draft-ryan-httpauth-payment", "tempoxyz/mpp", "mpp-specs",
    ))


def parse_atom_entries(body: bytes) -> list[dict[str, str]]:
    root = ElementTree.fromstring(body)
    if root.tag.lower().endswith("rss"):
        channel = root.find("channel")
        return [{"id": item.findtext("guid", default=item.findtext("link", default="")),
                 "title": item.findtext("title", default=""), "url": item.findtext("link", default=""),
                 "published_at": item.findtext("pubDate", default=""),
                 "summary": item.findtext("description", default="")}
                for item in (channel.findall("item") if channel is not None else [])]
    atom = "{http://www.w3.org/2005/Atom}"
    entries = []
    for entry in root.findall(f"{atom}entry"):
        link = entry.find(f"{atom}link")
        entries.append({
            "id": entry.findtext(f"{atom}id", default=""),
            "title": entry.findtext(f"{atom}title", default=""),
            "url": str(link.get("href") if link is not None else ""),
            "published_at": entry.findtext(f"{atom}published", default=entry.findtext(f"{atom}updated", default="")),
            "summary": entry.findtext(f"{atom}content", default=entry.findtext(f"{atom}summary", default="")),
        })
    return entries


def collect_ietf_payment_auth(since: datetime) -> list[dict[str, Any]]:
    body, _ = fetch(IETF_PAYMENT_DRAFT_URL)
    page = body.decode("utf-8", errors="replace")
    revision = re.search(r"draft-ryan-httpauth-payment-(\d{2})", page, re.IGNORECASE)
    updated = re.search(r"Last updated.*?(20\d{2}-\d{2}-\d{2})", page, re.IGNORECASE | re.DOTALL)
    published = parse_date(f"{updated.group(1)}T00:00:00Z") if updated else None
    if not revision or not published or published < since:
        return []
    return [{"source": "ietf_payment_auth", "external_id": f"draft-ryan-httpauth-payment-{revision.group(1)}",
             "title": f"IETF: Payment HTTP Authentication Scheme draft revision {revision.group(1)}",
             "url": IETF_PAYMENT_DRAFT_URL, "published_at": iso(published),
             "summary": "Active individual Internet-Draft; treat as work in progress, not a ratified standard.",
             "category": "specification", "news_eligible": True}]


def collect_github_atom(kind: str, since: datetime) -> list[dict[str, Any]]:
    source = "github_release" if kind == "releases" else "github_tag"
    items = []
    repos = dict.fromkeys((os.getenv("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPO), PAYMENTAUTH_GITHUB_REPO))
    for repo in repos:
        body, _ = fetch(f"https://github.com/{repo}/{kind}.atom")
        for entry in parse_atom_entries(body):
            published = parse_date(entry["published_at"])
            if not published or published < since:
                continue
            tag = entry["title"].strip() or entry["id"].rsplit("/", 1)[-1]
            items.append({"source": source, "external_id": f"{repo}:{tag}", "title": f"{repo} {kind[:-1]}: {tag}",
                          "url": normalize_url(entry["url"]), "published_at": iso(published),
                          "summary": entry["summary"][:2500], "category": "release" if source == "github_release" else "implementation_activity",
                          "news_eligible": True, "event_key": f"github:{repo}:{tag}"})
    return items


def collect_github_merged_prs(since: datetime) -> list[dict[str, Any]]:
    items, token = [], os.getenv("GITHUB_TOKEN") or None
    repos = dict.fromkeys((os.getenv("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPO), PAYMENTAUTH_GITHUB_REPO))
    for repo in repos:
        body, _ = fetch(f"https://api.github.com/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100", token=token)
        for pull in json.loads(body):
            merged = parse_date(str(pull.get("merged_at") or ""))
            text = f"{pull.get('title', '')} {pull.get('body', '')} {repo}"
            if not merged or merged < since or not has_explicit_tier_one_evidence(text):
                continue
            number = str(pull.get("number") or "")
            items.append({"source": "github_merged_pr", "external_id": f"{repo}:pr:{number}",
                          "title": f"{repo} merged PR #{number}: {pull.get('title', '')}",
                          "url": normalize_url(str(pull.get("html_url") or f"https://github.com/{repo}/pull/{number}")),
                          "published_at": iso(merged), "summary": str(pull.get("body") or pull.get("title") or "")[:2500],
                          "category": "implementation_activity", "news_eligible": True})
    return items


def configured_official_blog_feeds() -> list[str]:
    raw = os.getenv("OFFICIAL_BLOG_FEEDS", "").strip()
    if not raw:
        return []
    feeds = json.loads(raw)
    if not isinstance(feeds, list) or not all(isinstance(url, str) for url in feeds):
        raise ValueError("OFFICIAL_BLOG_FEEDS must be a JSON array of feed URLs")
    for feed in feeds:
        host = urllib.parse.urlsplit(feed).netloc.lower()
        if not any(host == allowed or host.endswith(f".{allowed}") for allowed in OFFICIAL_BLOG_HOSTS):
            raise ValueError(f"official blog feed host is not allowlisted: {host}")
    return feeds


def collect_official_blog_rss(since: datetime) -> list[dict[str, Any]]:
    items = []
    for feed in configured_official_blog_feeds():
        body, _ = fetch(feed)
        for entry in parse_atom_entries(body):
            published, text = parse_date(entry["published_at"]), f"{entry['title']} {entry['summary']}"
            if not published or published < since or not has_explicit_tier_one_evidence(text):
                continue
            items.append({"source": "official_blog", "external_id": entry["id"] or entry["url"], "title": entry["title"],
                          "url": normalize_url(entry["url"]), "published_at": iso(published), "summary": entry["summary"][:2500],
                          "category": "official_blog", "news_eligible": True})
    return items


def parse_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def is_relevant_payment_news(text: str) -> bool:
    normalized = text.lower()
    if "mpp.dev" in normalized or "paymentauth.org" in normalized:
        return True
    if re.search(r"\bmpp\b", normalized):
        return True
    if re.search(r"\bmachine payments?(?: protocol)?\b", normalized):
        return True
    if re.search(r"\bagentic? payments?\b|\bagent payments?\b", normalized):
        return True
    if re.search(r"\b(?:http[\s.-]*)?402\b|\bx[.]?402\b", normalized):
        return True
    if re.search(r"\bdraft-(?:ryan-)?httpauth-payment(?:-00)?\b", normalized):
        return True
    if any(name in normalized and organization in normalized for name, organization in WATCHED_PEOPLE):
        return True
    return bool(
        re.search(r"\btempo\b", normalized)
        and re.search(r"\b(payment|protocol|stablecoin|agent|crypto|settlement)\w*\b", normalized)
    )


def collect_hacker_news(since: datetime) -> list[dict[str, Any]]:
    items = []
    for query in NEWS_QUERIES:
        params = urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": "50"})
        body, _ = fetch(f"{HACKER_NEWS_SEARCH_URL}?{params}")
        for hit in json.loads(body).get("hits", []):
            published = parse_date(str(hit.get("created_at") or ""))
            if not published or published < since:
                continue
            searchable = " ".join(
                [str(hit.get("title") or ""), str(hit.get("story_text") or "")]
            )
            if not is_relevant_payment_news(searchable):
                continue
            object_id = str(hit.get("objectID") or "")
            url = normalize_url(
                str(hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}")
            )
            items.append(
                {
                    "source": "hacker_news",
                    "external_id": object_id or url,
                    "title": f"Hacker News: {hit.get('title') or 'Untitled story'}",
                    "url": url,
                    "published_at": iso(published),
                    "summary": str(hit.get("story_text") or hit.get("title") or "")[:2500],
                    "category": "community",
                    "news_eligible": True,
                }
            )
    return items


def collect_reddit(since: datetime) -> list[dict[str, Any]]:
    items = []
    for query in NEWS_QUERIES:
        params = urllib.parse.urlencode(
            {"q": query, "sort": "new", "t": "week", "limit": "100"}
        )
        body, _ = fetch(f"{REDDIT_SEARCH_URL}?{params}")
        root = ElementTree.fromstring(body)
        for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
            published = parse_date(
                entry.findtext("{http://www.w3.org/2005/Atom}updated", default="")
            )
            if not published or published < since:
                continue
            link_node = entry.find("{http://www.w3.org/2005/Atom}link")
            url = normalize_url(str(link_node.get("href") if link_node is not None else ""))
            title = entry.findtext("{http://www.w3.org/2005/Atom}title", default="")
            if not url or not title:
                continue
            items.append(
                {
                    "source": "reddit",
                    "external_id": url,
                    "title": f"Reddit: {title}",
                    "url": url,
                    "published_at": iso(published),
                    "summary": "Recent Reddit discussion found through Reddit search.",
                    "category": "community",
                    "news_eligible": True,
                }
            )
    return items


def collect_x(since: datetime) -> list[dict[str, Any]]:
    token = os.getenv("X_BEARER_TOKEN", "").strip()
    if not token:
        print(json.dumps({"level": "INFO", "source": "x", "status": "disabled_no_token"}))
        return []
    query = (
        '(MPP OR "machine payments protocol" OR x402 OR "x.402" OR "paymentauth.org" '
        'OR "HTTP 402" OR "draft-httpauth-payment-00" OR "draft-ryan-httpauth-payment" '
        'OR from:jeff_weinstein OR from:stevekaliski '
        'OR ("Brendan Ryan" Tempo) OR ("Jake Moxey" Tempo) OR ("Tom Meagher" Tempo)) '
        "-is:retweet lang:en"
    )
    params = urllib.parse.urlencode(
        {
            "query": query,
            "start_time": iso(since),
            "tweet.fields": "created_at,public_metrics,author_id",
            "max_results": "50",
        }
    )
    body, _ = fetch(f"{X_SEARCH_URL}?{params}", token=token)
    items = []
    for tweet in json.loads(body).get("data", []):
        tweet_id = str(tweet.get("id") or "")
        published = parse_date(str(tweet.get("created_at") or ""))
        if not tweet_id or not published or published < since:
            continue
        text = str(tweet.get("text") or "")
        items.append(
            {
                "source": "x",
                "external_id": tweet_id,
                "title": f"X: {text[:180]}",
                "url": f"https://x.com/i/web/status/{tweet_id}",
                "published_at": iso(published),
                "summary": text[:2500],
                "category": "community",
                "news_eligible": True,
            }
        )
    return items


def score(item: dict[str, Any]) -> int:
    base = {
        "mpp_catalog": 55,
        "tempo_blog": 50,
        "github": 45,
        "hacker_news": 30,
        "reddit": 25,
        "x": 25,
        "ietf_payment_auth": 55,
        "github_release": 55,
        "github_tag": 40,
        "github_merged_pr": 45,
        "official_blog": 50,
    }.get(item["source"], 20)
    text = f"{item['title']} {item['summary']}".lower()
    for terms, points in (
        (("release", "launch", "available"), 12),
        (("spec", "protocol", "sdk"), 10),
        (("payment", "settlement", "stablecoin"), 8),
        (("security", "vulnerability"), 12),
        (("partner", "integration"), 8),
    ):
        if any(term in text for term in terms):
            base += points
    return min(base, 100)


def persist_new_items(items: list[dict[str, Any]], seen_at: str) -> list[dict[str, Any]]:
    new_items = []
    for item in items:
        item_id = content_id(item)
        record = {
            "pk": f"ITEM#{item_id}",
            "entity_type": "ITEM",
            "item_id": item_id,
            "first_seen_at": seen_at,
            "importance_score": score(item),
            **item,
        }
        try:
            TABLE.put_item(Item=record, ConditionExpression="attribute_not_exists(pk)")
            new_items.append(record)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    return new_items


def fallback_summary(report_date: str, window_days: int, items: list[dict[str, Any]]) -> str:
    label = period_label(window_days)
    if not items:
        return (
            f"MPP Pulse {label} — period ending {report_date}\n\n"
            f"Pulse signal: no verified new development was found in the last {window_days} day"
            f"{'s' if window_days != 1 else ''}. "
            "The MPP catalog is tracked as inventory, not treated as news."
        )
    lines = [
        f"MPP Pulse {label} — period ending {report_date}",
        "",
        f"Top {label.lower()} signals:",
    ]
    for index, item in enumerate(items[:MAX_REPORT_ITEMS], 1):
        lines.append(
            f"[S{index}] {item['title']} (score {item['importance_score']}) — {item['url']}"
        )
    return "\n".join(lines)


def summarize(report_date: str, window_days: int, items: list[dict[str, Any]]) -> str:
    if not items:
        return fallback_summary(report_date, window_days, items)
    evidence = [
        {
            "label": f"S{index}",
            "title": item["title"],
            "url": item["url"],
            "source": item["source"],
            "score": item["importance_score"],
            "summary": item["summary"][:900],
        }
        for index, item in enumerate(items[:MAX_REPORT_ITEMS], 1)
    ]
    prompt = f"""Create a concise {period_label(window_days).lower()} machine-payments intelligence
brief for the {window_days}-day window ending {report_date}.
Use only the evidence JSON below. Cite every factual claim with [S#]. Clearly label inference.
Start with exactly one line beginning `Pulse signal:` that summarizes the period's most important
verified change, or says that no verified new development was found.
Include: Executive signal, Material developments, Why it matters, What to verify next, Sources.
Only treat evidence marked news_eligible as recent news. Do not call a catalog listing a provider,
launch, adoption event, or payment integration. The MPP service catalog is inventory; a runtime
HTTP 402 challenge or an authoritative dated announcement is needed to establish payment terms.
Do not describe a proposal or commit as shipped unless the evidence says so.
Keep the complete report under 1,000 words and finish with the Sources section.

{json.dumps(evidence, ensure_ascii=False)}
"""
    try:
        response = BEDROCK.converse(
            modelId=os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 2400, "temperature": 0.2, "topP": 0.9},
        )
        text = response["output"]["message"]["content"][0]["text"]
        ledger = "\n".join(
            f"[S{index}] {item['title']} — {item['url']}"
            for index, item in enumerate(items[:MAX_REPORT_ITEMS], 1)
        )
        return f"{text}\n\n## Source ledger\n{ledger}"
    except (ClientError, KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"level": "ERROR", "component": "bedrock", "error": str(exc)[:500]}))
        return fallback_summary(report_date, window_days, items)


def markdown_to_html(text: str) -> str:
    rendered = []
    for raw_line in text.splitlines():
        line = html.escape(raw_line.strip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(
            r"(https://[^\s<]+)",
            r'<a href="\1" rel="noreferrer">\1</a>',
            line,
        )
        if line.startswith("#### "):
            rendered.append(f"<h3>{line[5:]}</h3>")
        elif line.startswith("### "):
            rendered.append(f"<h2>{line[4:]}</h2>")
        elif line.startswith("## "):
            rendered.append(f"<h2>{line[3:]}</h2>")
        elif line == "---":
            rendered.append("<hr>")
        elif not line:
            continue
        else:
            rendered.append(f"<p>{line}</p>")
    return "\n".join(rendered)


def render_html(report_date: str, window_days: int, summary: str, run_id: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>MPP Pulse {html.escape(period_label(window_days))} — {html.escape(report_date)}</title>
<style>
body{{max-width:880px;margin:40px auto;padding:0 20px;font:16px/1.6 system-ui;color:#172033}}
h1{{color:#635bff}}h2{{margin-top:2rem}}h3{{margin-top:1.5rem}}p{{margin:.45rem 0}}
hr{{border:0;border-top:1px solid #d0d5dd;margin:2rem 0}}a{{color:#5145cd;word-break:break-all}}
footer{{margin-top:40px;color:#667085;border-top:1px solid #d0d5dd;padding-top:1rem}}
</style></head><body>
<h1>MPP Pulse {html.escape(period_label(window_days))} — period ending {html.escape(report_date)}</h1>
<main>{markdown_to_html(summary)}</main>
<footer>Generated autonomously by MPP Pulse · Run {html.escape(run_id)}</footer>
</body></html>"""


def send_report_email(
    report_date: str, window_days: int, summary: str, report_url: str
) -> dict[str, Any]:
    recipient = os.getenv("EMAIL_TO", "").strip()
    sender = os.getenv("EMAIL_FROM", "").strip()
    if not recipient or not sender:
        return {"status": "disabled", "reason": "EMAIL_TO or EMAIL_FROM is not configured"}

    label = period_label(window_days)
    subject = f"MPP Pulse {label} Intelligence | Period Ending {report_date} | MPP, x402 & Payment Auth"
    preview = next(
        (
            line.removeprefix("Pulse signal:").strip()
            for line in summary.splitlines()
            if line.lower().startswith("pulse signal:")
        ),
        "No verified new machine-payments signal was observed.",
    )
    text = (
        f"MPP Pulse {label} — period ending {report_date}\n\n"
        f"This {label.lower()} pulse signal: {preview}\n\n"
        f"Your {label.lower()} intelligence brief covers the emerging machine-payments layer, including "
        "MPP, Tempo, x402, Payment Auth, HTTP 402, specification work, and community signals.\n\n"
        f"Read the complete cited report: {report_url}\n\n"
        f"{summary}\n\n"
        "MPP Pulse is an autonomous machine-payments intelligence service."
    )
    html_body = (
        f"<h1>MPP Pulse {html.escape(label)} — period ending {html.escape(report_date)}</h1>"
        f"<p><strong>This {html.escape(label.lower())} pulse signal:</strong> {html.escape(preview)}</p>"
        f"<p>Your {html.escape(label.lower())} intelligence brief covers MPP, Tempo, x402, Payment Auth, HTTP 402, "
        "specification work, and relevant community signals.</p>"
        f'<p><a href="{html.escape(report_url)}">Read the complete cited report</a></p>'
        f"<hr>{markdown_to_html(summary)}"
        "<p>MPP Pulse is an autonomous machine-payments intelligence service.</p>"
    )
    try:
        SES.send_email(
            Source=sender,
            Destination={"ToAddresses": [recipient]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
    except ClientError as exc:
        error = exc.response.get("Error", {})
        return {
            "status": "failed",
            "recipient": recipient,
            "subject": subject,
            "error": f"{error.get('Code', 'EmailError')}: {error.get('Message', str(exc))}"[:500],
        }
    return {"status": "sent", "recipient": recipient, "subject": subject}


def send_draft_review_email(
    report_date: str,
    window_days: int,
    summary: str,
    report_url: str,
    evidence_count: int,
) -> dict[str, Any]:
    recipient = os.getenv("EMAIL_TO", "").strip()
    sender = os.getenv("EMAIL_FROM", "").strip()
    if not recipient or not sender:
        return {"status": "disabled", "reason": "EMAIL_TO or EMAIL_FROM is not configured"}
    label = period_label(window_days)
    subject = f"[DRAFT REVIEW] MPP Pulse {label} | Period Ending {report_date}"
    text = (
        f"DRAFT REVIEW: MPP Pulse {label} report ending {report_date}\n\n"
        f"Evidence selected by operator review: {evidence_count}\n"
        "This draft was generated from Google Sheet rows marked KEEP. It has not been sent to subscribers.\n\n"
        f"Review the private HTML draft: {report_url}\n\n"
        f"{summary}"
    )
    html_body = (
        f"<h1>DRAFT REVIEW: MPP Pulse {html.escape(label)}</h1>"
        f"<p>Period ending {html.escape(report_date)}</p>"
        f"<p>Evidence selected by operator review: {evidence_count}</p>"
        "<p>This draft has not been sent to subscribers.</p>"
        f'<p><a href="{html.escape(report_url)}">Review the private HTML draft</a></p>'
        f"<hr>{markdown_to_html(summary)}"
    )
    try:
        SES.send_email(
            Source=sender,
            Destination={"ToAddresses": [recipient]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
    except ClientError as exc:
        error = exc.response.get("Error", {})
        return {
            "status": "failed",
            "recipient": recipient,
            "subject": subject,
            "error": f"{error.get('Code', 'EmailError')}: {error.get('Message', str(exc))}"[:500],
        }
    return {"status": "sent", "recipient": recipient, "subject": subject}


def write_curation_snapshot(
    report_date: str, run_id: str, window_days: int, items: list[dict[str, Any]]
) -> str:
    snapshot_key = f"drafts/{report_date}/{run_id}.json"
    snapshot = {
        "report_date": report_date,
        "run_id": run_id,
        "window_days": window_days,
        "source": "google_sheets_evidence_inbox",
        "selected_items": [
            {
                "item_id": item["item_id"],
                "source": item["source"],
                "title": item["title"],
                "url": item["url"],
                "published_at": item["published_at"],
                "review_status": item["review_status"],
            }
            for item in items
        ],
    }
    S3.put_object(
        Bucket=os.environ["BUCKET_NAME"],
        Key=snapshot_key,
        Body=json.dumps(snapshot, ensure_ascii=False, indent=2).encode(),
        ContentType="application/json; charset=utf-8",
        ServerSideEncryption="AES256",
    )
    return snapshot_key


def load_report_items(window_start: datetime, window_end_exclusive: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    scan_args: dict[str, Any] = {}
    while True:
        response = TABLE.scan(**scan_args)
        for item in response.get("Items", []):
            if item.get("entity_type") != "ITEM" or not item.get("news_eligible", False):
                continue
            published = parse_date(str(item.get("published_at") or ""))
            if not published or not window_start <= published < window_end_exclusive:
                continue
            items.append(item)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_args["ExclusiveStartKey"] = last_key
    deduped = {item["item_id"]: item for item in items}
    report_items = list(deduped.values())
    report_items.sort(key=lambda item: item["importance_score"], reverse=True)
    return report_items


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    started = now_utc()
    run_id = str(event.get("run_id") or uuid.uuid4())
    force = bool(event.get("force", False))
    trigger = str(event.get("trigger") or "manual")
    mode, window_days, window_end, window_start, window_end_exclusive = resolve_run_window(
        event, started
    )
    report_date = window_end.isoformat()

    if not force:
        try:
            TABLE.put_item(
                Item={
                    "pk": run_lock_key(mode, window_end, window_days),
                    "entity_type": "LOCK",
                    "run_id": run_id,
                    "mode": mode,
                    "window_days": window_days,
                    "window_end": report_date,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return {
                    "status": "skipped",
                    "reason": "run_lock_exists",
                    "mode": mode,
                    "window_end": report_date,
                    "window_days": window_days,
                }
            raise

    errors: list[dict[str, str]] = []
    result: dict[str, Any] = {
        "run_id": run_id,
        "trigger": trigger,
        "mode": mode,
        "report_date": report_date,
        "window_days": window_days,
        "window_start": iso(window_start),
        "window_end_exclusive": iso(window_end_exclusive),
    }

    if mode == "collect":
        collection_since = started - timedelta(days=window_days)
        collected: list[dict[str, Any]] = []
        collectors = (
            ("mpp_catalog", collect_mpp),
            ("tempo_blog", lambda: collect_tempo(collection_since)),
            ("github", lambda: collect_github(collection_since)),
            ("ietf_payment_auth", lambda: collect_ietf_payment_auth(collection_since)),
            ("github_release", lambda: collect_github_atom("releases", collection_since)),
            ("github_tag", lambda: collect_github_atom("tags", collection_since)),
            ("github_merged_pr", lambda: collect_github_merged_prs(collection_since)),
            ("official_blog", lambda: collect_official_blog_rss(collection_since)),
            ("hacker_news", lambda: collect_hacker_news(collection_since)),
            ("reddit", lambda: collect_reddit(collection_since)),
            ("x", lambda: collect_x(collection_since)),
        )
        for name, collector in collectors:
            try:
                collected.extend(collector())
            except Exception as exc:
                print(json.dumps({"level": "ERROR", "collector": name, "error": str(exc)[:500]}))
                errors.append({"collector": name, "error": f"{type(exc).__name__}: {exc}"[:500]})
        new_items = persist_new_items(collected, iso(started))
        sheet_sync = sync_evidence_to_sheet(collected, run_id, iso(started))
        if sheet_sync.get("status") == "failed":
            errors.append({"collector": "google_sheets", "error": str(sheet_sync.get("error", "sync failed"))})
        result.update(
            {
                "collected": len(collected),
                "new_items": len(new_items),
                "collection_since": iso(collection_since),
                "collector_errors": errors,
                "google_sheets": sheet_sync,
            }
        )
    elif mode == "draft":
        selection = load_kept_sheet_items(window_start, window_end_exclusive)
        if selection["status"] != "ready":
            result.update({"draft": selection, "collector_errors": errors})
            result["status"] = "NOT_READY"
        elif not selection["items"]:
            result.update(
                {
                    "draft": {**selection, "status": "no_approved_evidence"},
                    "collector_errors": errors,
                }
            )
            result["status"] = "NO_APPROVED_EVIDENCE"
        else:
            report_items = selection["items"]
            summary = summarize(report_date, window_days, report_items)
            draft_summary = (
                "## DRAFT REVIEW\n\n"
                "This report was generated from Google Sheet rows marked KEEP. "
                "It has not been sent to subscribers.\n\n"
                f"{summary}"
            )
            report_html = render_html(report_date, window_days, draft_summary, run_id)
            report_key = f"drafts/{report_date}/{run_id}.html"
            S3.put_object(
                Bucket=os.environ["BUCKET_NAME"],
                Key=report_key,
                Body=report_html.encode(),
                ContentType="text/html; charset=utf-8",
                ServerSideEncryption="AES256",
            )
            snapshot_key = write_curation_snapshot(report_date, run_id, window_days, report_items)
            report_url = S3.generate_presigned_url(
                "get_object",
                Params={"Bucket": os.environ["BUCKET_NAME"], "Key": report_key},
                ExpiresIn=60 * 60 * 24 * 7,
            )
            email = send_draft_review_email(
                report_date,
                window_days,
                summary,
                report_url,
                len(report_items),
            )
            if email.get("status") == "failed":
                errors.append({"collector": "draft_email", "error": str(email.get("error", "email delivery failed"))})
            result.update(
                {
                    "draft": {
                        "status": "generated",
                        "kept_rows": selection["kept_rows"],
                        "eligible_kept_rows": selection["eligible_kept_rows"],
                    },
                    "news_items": len(report_items),
                    "collector_errors": errors,
                    "report_key": report_key,
                    "snapshot_key": snapshot_key,
                    "email": email,
                }
            )
    else:
        report_items = load_report_items(window_start, window_end_exclusive)
        summary = summarize(report_date, window_days, report_items)
        report_html = render_html(report_date, window_days, summary, run_id)
        report_key = f"reports/{report_date}/{run_id}.html"
        S3.put_object(
            Bucket=os.environ["BUCKET_NAME"],
            Key=report_key,
            Body=report_html.encode(),
            ContentType="text/html; charset=utf-8",
            ServerSideEncryption="AES256",
        )
        report_url = S3.generate_presigned_url(
            "get_object",
            Params={"Bucket": os.environ["BUCKET_NAME"], "Key": report_key},
            ExpiresIn=60 * 60 * 24 * 7,
        )
        email = send_report_email(report_date, window_days, summary, report_url)
        if email.get("status") == "failed":
            errors.append({"collector": "email", "error": str(email.get("error", "email delivery failed"))})
        result.update(
            {
                "news_items": len(report_items),
                "collector_errors": errors,
                "report_key": report_key,
                "email": email,
            }
        )

    if "status" not in result:
        result["status"] = "SUCCEEDED" if not errors else "PARTIAL_SUCCESS"
    TABLE.put_item(
        Item={
            "pk": f"RUN#{run_id}",
            "entity_type": "RUN",
            "created_at": iso(started),
            **result,
        }
    )
    print(json.dumps(result))
    return result
