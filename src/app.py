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

import boto3
from botocore.exceptions import ClientError


USER_AGENT = "mpp-pulse/0.1 (+https://github.com/OWNER/mpp-pulse)"
MPP_CATALOG_URL = "https://mpp.dev/api/services"
TEMPO_BLOG_URL = "https://tempo.xyz/blog"
DEFAULT_GITHUB_REPO = "tempoxyz/mpp"

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
                "category": "service",
            }
        )
    return items


def collect_tempo() -> list[dict[str, Any]]:
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

    return [
        {
            "source": "tempo_blog",
            "external_id": url,
            "title": title,
            "url": url,
            "published_at": iso(now_utc()),
            "summary": "Official Tempo blog entry discovered on the blog index.",
            "category": "ecosystem",
        }
        for url, title in list(links.items())[:20]
    ]


def collect_github(since: datetime) -> list[dict[str, Any]]:
    repo = os.getenv("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPO)
    params = urllib.parse.urlencode({"since": iso(since), "per_page": "20"})
    token = os.getenv("GITHUB_TOKEN") or None
    body, _ = fetch(f"https://api.github.com/repos/{repo}/commits?{params}", token=token)
    payload = json.loads(body)
    if not isinstance(payload, list):
        raise ValueError("GitHub commits endpoint returned a non-list response")

    items = []
    for record in payload:
        commit = record.get("commit") or {}
        author = commit.get("author") or {}
        message = str(commit.get("message") or "")
        sha = str(record.get("sha") or "")
        items.append(
            {
                "source": "github",
                "external_id": sha,
                "title": f"{repo}: {message.splitlines()[0][:180]}",
                "url": normalize_url(str(record.get("html_url") or f"https://github.com/{repo}")),
                "published_at": str(author.get("date") or iso(now_utc())),
                "summary": message[:2500],
                "category": "code",
            }
        )
    return items


def score(item: dict[str, Any]) -> int:
    base = {"mpp_catalog": 55, "tempo_blog": 50, "github": 45}.get(item["source"], 20)
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
        return f"MPP Pulse for {report_date}: no newly observed evidence was found."
    lines = [f"MPP Pulse — {report_date}", "", "Top newly observed signals:"]
    for index, item in enumerate(items[:10], 1):
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
        for index, item in enumerate(items[:10], 1)
    ]
    prompt = f"""Create a concise daily machine-payments intelligence brief for {report_date}.
Use only the evidence JSON below. Cite every factual claim with [S#]. Clearly label inference.
Include: Executive signal, Material developments, Why it matters, What to verify next, Sources.
Do not describe a proposal or commit as shipped unless the evidence says so.
Keep the complete report under 650 words and finish with the Sources section.

{json.dumps(evidence, ensure_ascii=False)}
"""
    try:
        response = BEDROCK.converse(
            modelId=os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 1600, "temperature": 0.2, "topP": 0.9},
        )
        text = response["output"]["message"]["content"][0]["text"]
        ledger = "\n".join(
            f"[S{index}] {item['title']} — {item['url']}"
            for index, item in enumerate(items[:10], 1)
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
        f"MPP Pulse Daily Intelligence | {report_date} | Machine Payments, MPP & Tempo Signal | $25/mo"
    )
    preview = summary.split("\n", 1)[0].strip() or "No new machine-payments signal was observed."
    text = (
        f"MPP Pulse — {report_date}\n\n"
        f"Today's pulse signal: {preview}\n\n"
        "Your daily intelligence brief covers the emerging machine-payments layer, including "
        "MPP services, Tempo ecosystem activity, and relevant GitHub development.\n\n"
        f"Read the complete cited report: {report_url}\n\n"
        f"{summary}\n\n"
        "MPP Pulse is an autonomous daily intelligence service."
    )
    html_body = (
        f"<h1>MPP Pulse — {html.escape(report_date)}</h1>"
        f"<p><strong>Today's pulse signal:</strong> {html.escape(preview)}</p>"
        "<p>Your daily intelligence brief covers the emerging machine-payments layer, "
        "including MPP services, Tempo ecosystem activity, and relevant GitHub development.</p>"
        f'<p><a href="{html.escape(report_url)}">Read the complete cited report</a></p>'
        f"<hr>{markdown_to_html(summary)}"
        "<p>MPP Pulse is an autonomous daily intelligence service.</p>"
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

    if not force:
        try:
            TABLE.put_item(
                Item={"pk": f"LOCK#{report_date}", "entity_type": "LOCK", "run_id": run_id},
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return {"status": "skipped", "reason": "daily_lock_exists", "date": report_date}
            raise

    errors = []
    collected: list[dict[str, Any]] = []
    collectors = (
        ("mpp_catalog", collect_mpp),
        ("tempo_blog", collect_tempo),
        ("github", lambda: collect_github(started - timedelta(hours=36))),
    )
    for name, collector in collectors:
        try:
            collected.extend(collector())
        except Exception as exc:
            print(json.dumps({"level": "ERROR", "collector": name, "error": str(exc)[:500]}))
            errors.append({"collector": name, "error": f"{type(exc).__name__}: {exc}"[:500]})

    new_items = persist_new_items(collected, iso(started))
    new_items.sort(key=lambda item: item["importance_score"], reverse=True)
    report_items = new_items
    if event.get("resummarize") and not report_items:
        report_items = [{**item, "importance_score": score(item)} for item in collected]
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
        "collected": len(collected),
        "new_items": len(new_items),
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
