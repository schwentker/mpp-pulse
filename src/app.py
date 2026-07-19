from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
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
LOOKBACK_DAYS = 7
MAX_REPORT_ITEMS = 50

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
S3 = boto3.client("s3")
BEDROCK = boto3.client("bedrock-runtime")
SES = boto3.client("ses")


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


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


def collect_mpp() -> list[dict[str, Any]]:
    body, _ = fetch(MPP_CATALOG_URL)
    payload = json.loads(body)
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
                    str(service.get("url") or service.get("homepage") or MPP_CATALOG_URL)
                ),
                "published_at": iso(now_utc()),
                "summary": json.dumps(service, sort_keys=True, default=str)[:2500],
                "category": "catalog_snapshot",
                "news_eligible": False,
            }
        )
    return items


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


def fallback_summary(report_date: str, items: list[dict[str, Any]]) -> str:
    if not items:
        return (
            f"MPP Pulse — {report_date}\n\n"
            "Pulse signal: no verified new development was found in the last seven days. "
            "The MPP catalog is tracked as inventory, not treated as news."
        )
    lines = [f"MPP Pulse Weekly — week ending {report_date}", "", "Top weekly signals:"]
    for index, item in enumerate(items[:MAX_REPORT_ITEMS], 1):
        lines.append(
            f"[S{index}] {item['title']} (score {item['importance_score']}) — {item['url']}"
        )
    return "\n".join(lines)


def summarize(report_date: str, items: list[dict[str, Any]]) -> str:
    if not items:
        return fallback_summary(report_date, items)
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
    prompt = f"""Create a concise weekly machine-payments intelligence brief for the seven-day
window ending {report_date}.
Use only the evidence JSON below. Cite every factual claim with [S#]. Clearly label inference.
Start with exactly one line beginning `Pulse signal:` that summarizes the week's most important
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
        return fallback_summary(report_date, items)


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


def render_html(report_date: str, summary: str, run_id: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>MPP Pulse — {html.escape(report_date)}</title>
<style>
body{{max-width:880px;margin:40px auto;padding:0 20px;font:16px/1.6 system-ui;color:#172033}}
h1{{color:#635bff}}h2{{margin-top:2rem}}h3{{margin-top:1.5rem}}p{{margin:.45rem 0}}
hr{{border:0;border-top:1px solid #d0d5dd;margin:2rem 0}}a{{color:#5145cd;word-break:break-all}}
footer{{margin-top:40px;color:#667085;border-top:1px solid #d0d5dd;padding-top:1rem}}
</style></head><body>
<h1>MPP Pulse — {html.escape(report_date)}</h1>
<main>{markdown_to_html(summary)}</main>
<footer>Generated autonomously by MPP Pulse · Run {html.escape(run_id)}</footer>
</body></html>"""


def send_report_email(report_date: str, summary: str, report_url: str) -> dict[str, Any]:
    recipient = os.getenv("EMAIL_TO", "").strip()
    sender = os.getenv("EMAIL_FROM", "").strip()
    if not recipient or not sender:
        return {"status": "disabled", "reason": "EMAIL_TO or EMAIL_FROM is not configured"}

    subject = (
        f"MPP Pulse Weekly Intelligence | Week Ending {report_date} | MPP, x402 & Payment Auth"
    )
    preview = next(
        (
            line.removeprefix("Pulse signal:").strip()
            for line in summary.splitlines()
            if line.lower().startswith("pulse signal:")
        ),
        "No verified new machine-payments signal was observed.",
    )
    text = (
        f"MPP Pulse Weekly — week ending {report_date}\n\n"
        f"This week's pulse signal: {preview}\n\n"
        "Your weekly intelligence brief covers the emerging machine-payments layer, including "
        "MPP, Tempo, x402, Payment Auth, HTTP 402, specification work, and community signals.\n\n"
        f"Read the complete cited report: {report_url}\n\n"
        f"{summary}\n\n"
        "MPP Pulse is an autonomous weekly intelligence service."
    )
    html_body = (
        f"<h1>MPP Pulse Weekly — week ending {html.escape(report_date)}</h1>"
        f"<p><strong>This week's pulse signal:</strong> {html.escape(preview)}</p>"
        "<p>Your weekly intelligence brief covers MPP, Tempo, x402, Payment Auth, HTTP 402, "
        "specification work, and relevant community signals.</p>"
        f'<p><a href="{html.escape(report_url)}">Read the complete cited report</a></p>'
        f"<hr>{markdown_to_html(summary)}"
        "<p>MPP Pulse is an autonomous weekly intelligence service.</p>"
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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    started = now_utc()
    report_date = str(event.get("report_date") or started.date().isoformat())
    run_id = str(event.get("run_id") or uuid.uuid4())
    force = bool(event.get("force", False))
    trigger = str(event.get("trigger") or "manual")
    window_days = int(event.get("window_days") or LOOKBACK_DAYS)
    since = started - timedelta(days=window_days)

    if not force:
        try:
            TABLE.put_item(
                Item={
                    "pk": f"LOCK#WEEK#{report_date}",
                    "entity_type": "LOCK",
                    "run_id": run_id,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return {"status": "skipped", "reason": "weekly_lock_exists", "date": report_date}
            raise

    errors = []
    collected: list[dict[str, Any]] = []
    collectors = (
        ("mpp_catalog", collect_mpp),
        ("tempo_blog", lambda: collect_tempo(since)),
        ("github", lambda: collect_github(since)),
        ("hacker_news", lambda: collect_hacker_news(since)),
        ("reddit", lambda: collect_reddit(since)),
        ("x", lambda: collect_x(since)),
    )
    for name, collector in collectors:
        try:
            collected.extend(collector())
        except Exception as exc:
            print(json.dumps({"level": "ERROR", "collector": name, "error": str(exc)[:500]}))
            errors.append({"collector": name, "error": f"{type(exc).__name__}: {exc}"[:500]})

    new_items = persist_new_items(collected, iso(started))
    new_items.sort(key=lambda item: item["importance_score"], reverse=True)
    report_items_by_id = {
        content_id(item): {**item, "importance_score": score(item)}
        for item in collected
        if item.get("news_eligible", False)
    }
    report_items = list(report_items_by_id.values())
    report_items.sort(key=lambda item: item["importance_score"], reverse=True)
    summary = summarize(report_date, report_items)
    report_html = render_html(report_date, summary, run_id)
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
    email = send_report_email(report_date, summary, report_url)
    if email.get("status") == "failed":
        errors.append({"collector": "email", "error": str(email.get("error", "email delivery failed"))})

    status = "SUCCEEDED" if not errors else "PARTIAL_SUCCESS"
    result = {
        "status": status,
        "run_id": run_id,
        "trigger": trigger,
        "report_date": report_date,
        "window_days": window_days,
        "collected": len(collected),
        "new_items": len(new_items),
        "news_items": len(report_items),
        "collector_errors": errors,
        "report_key": report_key,
        "email": email,
    }
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
