# MPP Pulse

[![Tests](https://github.com/schwentker/mpp-pulse/actions/workflows/ci.yml/badge.svg)](https://github.com/schwentker/mpp-pulse/actions/workflows/ci.yml)
[![AWS SAM](https://img.shields.io/badge/AWS-SAM-FF9900?logo=amazonaws)](template.yaml)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](src/app.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

MPP Pulse is open-source weekly intelligence on MPP, Tempo, x402, HTTP 402,
and the machine-payments ecosystem. It collects evidence on an independent
schedule, then generates a cited report from the persisted reporting window.

It is the open-source engine behind a weekly machine-payments intelligence
brief. To receive the report, subscriptions are opening soon.

The project was built for the AWS Builder Center Weekend Agent Challenge with a
deliberately small architecture: two independent schedules, one Lambda, DynamoDB, S3, and
Amazon Bedrock.

## What it does

The default configuration separates collection from reporting:

1. A daily collection run fetches and persists evidence without calling Bedrock
   or sending email.
2. A weekly report run reads persisted evidence from the configured seven-day
   window, asks Amazon Nova to prepare a cited briefing, emails it, and saves a
   private HTML report for review.
3. A manual draft run reads only Google Sheet rows marked `KEEP`, writes a
   private draft and curation snapshot, then sends review email only to the
   configured operator address.

Collection runs:

1. Fetch the public MPP services catalog.
2. Check the official Tempo blog index.
3. Retrieve recent commits from the MPP and PaymentAuth specification repositories.
4. Search Hacker News, Reddit, and optionally X for relevant signals.
5. Normalize URLs and create stable content identities.
6. Use conditional DynamoDB writes to suppress duplicate evidence storage.

Report runs:

1. Read eligible persisted evidence in the reporting window.
2. Apply deterministic importance scoring and rank up to 50 report items.
3. Send the ranked evidence packet to Amazon Nova 2 Lite.
4. Produce and email a cited report with an exact source ledger.
5. Save the finished report as private, encrypted HTML in Amazon S3.
6. Write structured run results to CloudWatch and DynamoDB.

Collectors fail independently. If one source is unavailable, the remaining
sources can still produce a `PARTIAL_SUCCESS` report. If Bedrock is unavailable,
the function produces a deterministic source-linked fallback report.

## Verified deployment

The reference deployment has been tested in `us-west-2`.

| Check | Result |
|---|---|
| CloudFormation stack | `UPDATE_COMPLETE` |
| Collection schedule | Daily, configurable |
| Report schedule | Weekly, configurable and disabled by default |
| Timezone | `America/Los_Angeles` |
| Model | `us.amazon.nova-2-lite-v1:0` |
| Autonomous evidence run | `SUCCEEDED` |
| Evidence collected | 121 records |
| Duplicate inserts on repeat run | 0 |
| Collector errors | 0 |
| Unit tests | 11 passing |
| SAM validation and build | Passing |

The earlier reference deployment used one weekly schedule. The current template
separates daily collection from report generation and keeps both schedules
disabled by default until explicitly enabled during deployment.

## Architecture

![MPP Pulse architecture](docs/screenshots/mpp-pulse-architecture.png)

AWS resources are defined in [template.yaml](template.yaml):

- One ARM64 AWS Lambda function
- Two EventBridge Scheduler schedules
- One DynamoDB on-demand table
- One private encrypted S3 bucket
- One CloudWatch Lambda error alarm
- Least-purpose IAM policies generated through AWS SAM

## Why primary sources only

The weekend build intentionally watches three high-signal surfaces:

- `https://mpp.dev/api/services`
- `https://tempo.xyz/blog`
- `https://github.com/tempoxyz/mpp`
- `https://github.com/tempoxyz/mpp-specs` (the source for `paymentauth.org`)

Hacker News and Reddit are optional community collectors in the deployed path,
with a strict seven-day window and relevance filtering. X/Twitter is supported
only when an official API bearer token is configured as `X_BEARER_TOKEN`.
LinkedIn and broad web search remain future adapters.

The protocol watchlist includes `mpp.dev`, “Machine Payments Protocol,” `x402`,
`x.402`, `paymentauth.org`, “HTTP 402,” `draft-httpauth-payment-00`, and
`draft-ryan-httpauth-payment`. Named-author signals are also tracked for Brendan
Ryan, Jake Moxey, and Tom Meagher with Tempo context, plus Jeff Weinstein and
Steve Kaliski with Stripe context. On X, the known accounts
`@jeff_weinstein` and `@stevekaliski` are included directly.

## Methodology

Evidence enters a report only with direct support: matching terms in the title,
URL, text, repository, or recognized author context. Primary sources outrank
community discussion. Catalog inventory is not treated as news. Proposals are
distinguished from shipped changes. Scoring weighs source authority, recency,
protocol significance, implementation activity, adoption signals, and payment
relevance; exact weights and tuned rules evolve privately.

## Evidence and AI safety

The language model does not discover facts. Collection, normalization,
deduplication, and ranking happen before Bedrock is called.

Nova receives a bounded JSON packet containing:

- Stable source labels such as `[S1]`
- Titles and canonical URLs
- Source type and deterministic score
- A bounded source summary

The prompt requires the model to:

- Use only supplied evidence
- Cite factual claims with `[S#]`
- Label inference explicitly
- Avoid treating a commit or proposal as a shipped feature
- Keep the report below 1,000 words

The application appends its own deterministic source ledger after model
generation. This guarantees that cited evidence retains exact URLs even if the
model formats its own source section imperfectly.

## Persistence and idempotency

DynamoDB uses a single string partition key:

| Record | Key pattern | Purpose |
|---|---|---|
| Collection lock | `LOCK#COLLECT#END#YYYY-MM-DD#WINDOW#N` | Prevent repeated collection for a period |
| Report lock | `LOCK#REPORT#END#YYYY-MM-DD#WINDOW#N` | Prevent repeated report generation for a period |
| Evidence | `ITEM#SHA256` | Store a stable version of observed content |
| Run | `RUN#UUID` | Record status, counts, errors, and report location |

Evidence identity excludes collection time. An unchanged service, blog entry,
or commit therefore receives the same content ID on later runs. Conditional
writes using `attribute_not_exists(pk)` prevent duplicate inserts.

Manual demonstration events can set:

```json
{
  "force": true,
  "window_days": 7
}
```

`force` bypasses the run lock. `mode` selects either `collect` or `report`.
`window_days` controls the relevant collection or reporting period. Collection
uses a rolling lookback from invocation time. Reports use a calendar period
ending on `window_end` or `report_date`. Collection and report windows have
independent SAM parameters.

S3 reports use:

```text
reports/YYYY-MM-DD/RUN_ID.html
```

Objects are private, encrypted with SSE-S3, and expire after 30 days.

## Repository structure

```text
mpp-pulse/
├── .github/workflows/ci.yml
├── docs/
│   ├── architecture.mmd
│   ├── article.md
│   ├── evidence-checklist.md
│   └── screenshots/
│       ├── 01-stack-complete.png
│       ├── 02-eventbridge-schedule.png
│       ├── 02a-eventbridge-enabled.png
│       ├── 03-html-report.png
│       └── 04-autonomous-invocation.png
├── events/manual.json
├── src/
│   ├── app.py
│   └── requirements.txt
├── tests/test_app.py
├── LICENSE
├── pytest.ini
├── README.md
└── template.yaml
```

## Prerequisites

- An AWS account
- AWS credentials allowed to deploy CloudFormation/SAM resources
- AWS CLI
- AWS SAM CLI
- Amazon Bedrock access in the deployment region
- Python 3.11 for tests and the Lambda build

The default region and model used by the reference deployment are:

```text
Region: us-west-2
Model:  us.amazon.nova-2-lite-v1:0
```

Confirm the model profile is available:

```bash
aws bedrock list-inference-profiles \
  --region us-west-2 \
  --type-equals SYSTEM_DEFINED \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId, `nova-2-lite`)]'
```

## Local validation

No third-party runtime packages are required. AWS Lambda supplies `boto3` and
`botocore`.

```bash
python3 -m pytest -q
sam validate --lint
sam build
```

Expected result:

```text
11 passed
Build Succeeded
template.yaml is a valid SAM Template
```

## Deploy safely

The template defaults both schedules to `DISABLED`. Deploy and verify manually
before allowing autonomous collection or report generation.

```bash
sam build

sam deploy \
  --stack-name mpp-pulse-dev \
  --region us-west-2 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --parameter-overrides \
      CollectScheduleState=DISABLED \
      ReportScheduleState=DISABLED \
      CollectScheduleExpression='cron(0 7 * * ? *)' \
      ReportScheduleExpression='cron(30 11 ? * SUN *)' \
      CollectionWindowDays=1 \
      ReportWindowDays=7 \
      BedrockModelId=us.amazon.nova-2-lite-v1:0 \
      ScheduleTimezone=America/Los_Angeles \
      GitHubRepository=tempoxyz/mpp
```

Read the generated resource names:

```bash
aws cloudformation describe-stacks \
  --stack-name mpp-pulse-dev \
  --region us-west-2 \
  --query 'Stacks[0].Outputs' \
  --output table
```

## Verify manually

Replace `FUNCTION_NAME` with the CloudFormation output:

```bash
aws lambda invoke \
  --function-name FUNCTION_NAME \
  --region us-west-2 \
  --payload file://events/collect.json \
  response.json
```

Confirm that the response contains:

```json
{
  "status": "SUCCEEDED",
  "mode": "collect",
  "collector_errors": []
}
```

Then invoke the report event after collection:

```bash
aws lambda invoke \
  --function-name FUNCTION_NAME \
  --region us-west-2 \
  --payload file://events/manual.json \
  response.json
```

Confirm that response includes:

```json
{
  "status": "SUCCEEDED",
  "mode": "report",
  "report_key": "reports/YYYY-MM-DD/RUN_ID.html"
}
```

Then verify:

1. CloudWatch contains the structured completion line.
2. DynamoDB contains a `RUN#...` record and evidence items.
3. S3 contains the reported HTML object.
4. A second forced run reports zero newly inserted unchanged items.

## Configure collection and report schedules

```bash
sam deploy \
  --stack-name mpp-pulse-dev \
  --region us-west-2 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --parameter-overrides \
      CollectScheduleState=ENABLED \
      ReportScheduleState=DISABLED \
      CollectScheduleExpression='cron(0 7 * * ? *)' \
      ReportScheduleExpression='cron(30 11 ? * SUN *)' \
      CollectionWindowDays=1 \
      ReportWindowDays=7 \
      BedrockModelId=us.amazon.nova-2-lite-v1:0 \
      ScheduleTimezone=America/Los_Angeles \
      GitHubRepository=tempoxyz/mpp
```

Verify the result:

```bash
aws scheduler get-schedule \
  --name mpp-pulse-dev-collect \
  --region us-west-2 \
  --query '{State:State,Expression:ScheduleExpression,Timezone:ScheduleExpressionTimezone}'
```

## Configuration

CloudFormation parameters:

| Parameter | Default | Description |
|---|---|---|
| `CollectScheduleState` | `DISABLED` | Safe initial collection schedule state |
| `ReportScheduleState` | `DISABLED` | Report scheduler state. Keep disabled until the manual review gate is implemented. |
| `ScheduleTimezone` | `America/Los_Angeles` | EventBridge evaluation timezone |
| `CollectScheduleExpression` | `cron(0 7 * * ? *)` | Daily collection cadence |
| `ReportScheduleExpression` | `cron(30 11 ? * SUN *)` | Weekly report cadence |
| `CollectionWindowDays` | `1` | Rolling collection lookback window |
| `ReportWindowDays` | `7` | Report evidence window |
| `GoogleSheetsEnabled` | `false` | Enable the private evidence review queue |
| `GoogleSheetId` | empty | Private Google Sheets spreadsheet ID |
| `GoogleCredentialsParameterName` | `mpp-pulse/google-service-account` | SSM SecureString containing service-account JSON |
| `BedrockModelId` | `us.amazon.nova-2-lite-v1:0` | Bedrock inference profile |
| `GitHubRepository` | `tempoxyz/mpp` | Repository watched for recent commits |

Lambda environment variables are populated from these parameters and generated
resource references.

## Google Sheet review queue

Stage 3 adds an optional private review queue. It writes only to the
`evidence_inbox` tab. Create one private workbook with these tabs before enabling
the integration:

```text
evidence_inbox
curation
subscribers
issue_queue
run_log
```

The Lambda never writes to `subscribers` in this stage. Protect that tab because
it will eventually contain personal email addresses from the Google Form.

The `evidence_inbox` header row is:

```text
item_id, run_id, collected_at, published_at, source, source_type,
title, canonical_url, supporting_excerpt, entity_match, confidence,
importance_score, eligibility, review_status, operator_note
```

Share the workbook only with the operator and the Google service-account email.
Store the downloaded service-account JSON as an SSM Parameter Store
`SecureString`; never commit it or print it in logs:

```bash
aws ssm put-parameter \
  --name mpp-pulse/google-service-account \
  --type SecureString \
  --value "$(<service-account.json)" \
  --overwrite \
  --region us-west-2
```

Enable the queue only after the workbook and parameter exist:

```bash
sam deploy \
  --stack-name mpp-pulse-dev \
  --region us-west-2 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --parameter-overrides \
      GoogleSheetsEnabled=true \
      GoogleSheetId=YOUR_PRIVATE_SHEET_ID \
      GoogleCredentialsParameterName=mpp-pulse/google-service-account \
      CollectScheduleState=ENABLED \
      ReportScheduleState=DISABLED
```

When disabled or incompletely configured, collection continues without a Sheet
write. When configured, repeat collection upserts by stable `item_id` and
preserves existing `review_status` and `operator_note` values.

## Manual draft review

The private workbook is the human approval gate. Mark a relevant
`evidence_inbox` row as `KEEP` after review. The manual draft event reads only
`KEEP` rows whose `eligibility` is `news` and whose publication date falls in
the configured reporting window. It ignores `PENDING`, `CUT`, `WATCH` and
inventory rows.

Run the draft manually after the service account and SSM parameter are
configured:

```bash
aws lambda invoke \
  --function-name FUNCTION_NAME \
  --region us-west-2 \
  --payload file://events/manual.json \
  response.json
```

On success, the function writes:

```text
drafts/YYYY-MM-DD/RUN_ID.html
drafts/YYYY-MM-DD/RUN_ID.json
```

The JSON file records the exact approved evidence used in the draft. The email
subject begins `[DRAFT REVIEW]` and goes only to `EMAIL_TO`. It never sends to
subscribers. If there are no approved rows, the run returns
`NO_APPROVED_EVIDENCE` without writing a draft or sending email.

## Test plan

1. Unit-test URL canonicalization, scoring, timestamp handling, content IDs,
   fallback citations, and HTML rendering.
2. Validate and build the SAM template.
3. Deploy with the schedule disabled.
4. Invoke with the supplied manual event.
5. Confirm run and evidence records in DynamoDB.
6. Confirm a rendered HTML object in S3.
7. Review CloudWatch for collector or Bedrock errors.
8. Repeat the collection run and confirm duplicate evidence is not inserted again.
9. Invoke a report run and confirm it only reads persisted evidence.
10. Enable the collection schedule. Keep the report schedule disabled until the review gate exists.
11. Capture a genuine EventBridge-triggered collection invocation.

## Cost and failure controls

- One daily collection invocation by default
- Report scheduler disabled by default
- Reserved Lambda concurrency of one
- 90-second Lambda timeout
- Six bounded source collectors
- At most 100 MPP catalog records
- At most 20 Tempo links
- At most 100 recent commits from each watched GitHub repository
- Up to 50 ranked evidence records sent to Bedrock
- Bedrock output capped at 2,400 tokens
- DynamoDB on-demand billing
- Private encrypted S3 storage
- 30-day S3 lifecycle expiration
- Deterministic report fallback if Bedrock fails
- Independent collector failure handling
- Disabled schedule on first deployment

## Rollback

Disable the schedule first:

```bash
sam deploy \
  --stack-name mpp-pulse-dev \
  --region us-west-2 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --parameter-overrides \
      CollectScheduleState=DISABLED \
      ReportScheduleState=DISABLED
```

Export any reports you want to keep, empty the generated report bucket, and
delete the stack:

```bash
aws s3 rm s3://BUCKET_NAME --recursive
sam delete --stack-name mpp-pulse-dev --region us-west-2
```

This removes the Lambda, schedule, DynamoDB table, S3 bucket, IAM roles, and
alarm created by the application stack.

## Deployment evidence

CloudFormation deployment:

![CloudFormation stack events](docs/screenshots/01-stack-complete.png)

Historic weekly schedule evidence:

![EventBridge enabled status](docs/screenshots/02a-eventbridge-enabled.png)

![EventBridge schedule](docs/screenshots/02-eventbridge-schedule.png)

Autonomous invocation:

![CloudWatch autonomous invocation](docs/screenshots/04-autonomous-invocation.png)

Generated report:

![MPP Pulse weekly report for the week ending July 19, 2026](docs/screenshots/mpp-pulse-weekly-20260719.jpg)

The complete evidence checklist is in
[docs/evidence-checklist.md](docs/evidence-checklist.md).

## Roadmap

- Detect material field-level changes in MPP catalog records
- Add GitHub release, pull request, and specification-change monitoring across
  MPP, PaymentAuth, HTTP 402, and x402
- Distinguish proposals, announcements, implementations, and verified deployments
- Improve evidence provenance, corroboration, and confidence classification
- Cluster related evidence across primary and community sources
- Surface longitudinal changes across providers, payment rails, and protocol
  adoption
- Explore MPP-native distribution experiments for selected research

## License

[MIT](LICENSE)
