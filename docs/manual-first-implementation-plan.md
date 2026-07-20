# Machine Commerce Report: Manual-First Implementation Plan

Status date: 2026-07-19

## Operating model

MPP Pulse remains the open-source AWS collection engine. Machine Commerce Report is the subscriber-facing publication. Sandbox Labs is the advisory practice.

The first operating loop is intentionally human-reviewed:

```text
Daily collect
    -> DynamoDB and S3 evidence
    -> Google Sheet review queue
    -> operator keeps, cuts or watchlists items
    -> weekly draft from kept items
    -> operator reviews draft
    -> manual beehiiv publication
```

Daily collection and weekly reporting are separate cadences. Collection uses a rolling lookback. Reporting uses a calendar window. Report scheduling remains disabled until the manual review gate is implemented.

## Current status

- Stage 1 complete: current pipeline mapped with no edits.
- Stage 2 complete locally: collection and report modes separated.
- Stage 2 validation: 17 tests passed, SAM validation passed and SAM build passed.
- Stage 3 complete locally: optional Google Sheet evidence review queue added.
- Stage 3 validation: 20 tests passed, SAM validation passed and SAM build passed.
- Private Google workbook has been created. Service-account creation and SSM configuration remain operator setup steps. The workbook URL and ID are deployment configuration, not repository content.
- Stage 4 complete locally: manual draft mode reads approved Sheet evidence and sends operator review only.
- Stage 4 validation: 23 tests passed, SAM validation passed and SAM build passed.
- Stages 2 through 4 are uncommitted because GitHub authentication must be repaired before publishing.
- Next stage: collector registry parity.

## Data ownership

- DynamoDB and S3 remain the technical evidence source of truth.
- Google Sheets is an operator review interface, not the only persistence layer.
- Use one private workbook with separate tabs: `evidence_inbox`, `curation`, `subscribers`, `issue_queue` and `run_log`.
- Google Form writes only to `subscribers`. Subscriber rows remain protected from ordinary evidence editing.
- Do not store service-account credentials, sheet IDs or subscriber exports in Git.

## Stage sequence

1. `01_pipeline_map.json`: read-only pipeline map. Complete.
2. `02_separate_collect_and_report_cadence.json`: separate rolling collection from calendar reporting. Complete locally.
3. `03_google_sheet_review_queue.json`: write normalized evidence to a private review workbook. Complete locally.
4. `04_manual_curation_to_weekly_draft.json`: generate a draft only from rows marked `KEEP`. Complete locally.
5. `05_collector_registry_parity.json`: replace handler branching with a declarative registry while proving existing-source parity.
6. `06_tier1_sources_and_precision_gates.json`: add IETF, GitHub release/tag/merged-PR and official-blog sources with deterministic eligibility gates.
7. `07_heartbeat_evals_and_ci.json`: add source heartbeats, golden fixtures, operator keep/cut labels and CI precision checks.

## Stage gates

Every stage must stop after implementation and validation. The operator chooses the next Codex model and effort in the UI before continuing. A stage may not deploy AWS resources, send subscriber email or publish a report without explicit approval.

Required response at every stop:

- files changed
- tests and validation results
- configuration or deployment implications
- open risks
- recommended next model and effort

## Runtime controls

- `COLLECTION_WINDOW_DAYS`: rolling collector lookback, default `1`.
- `REPORT_WINDOW_DAYS`: calendar report window, default `7`.
- `CollectScheduleExpression`: collection cadence.
- `ReportScheduleExpression`: report cadence.
- `CollectScheduleState`: disabled until deployment is intentionally enabled.
- `ReportScheduleState`: disabled until the manual review gate exists.
- `BEDROCK_MODEL_ID`: deployed Bedrock model, separate from the Codex model used to edit the repository.

## Google Sheet review schema

`evidence_inbox` fields:

```text
item_id, run_id, collected_at, published_at, source, source_type,
title, canonical_url, supporting_excerpt, entity_match, confidence,
importance_score, eligibility, review_status, operator_note
```

Allowed `review_status` values: `PENDING`, `KEEP`, `CUT`, `WATCH`.

`subscribers` fields:

```text
submitted_at, email, name, role, organization, watchlist_request,
consent, status, approved_at, notes
```

New subscribers begin as `PENDING`. Beehiiv import remains manual until the report quality and subscriber workflow are proven.

## Safety and rollback

- Keep report scheduling disabled during Stages 3 through 4.
- A Sheets API outage must not stop evidence persistence or mark the collection run as failed if the evidence was safely stored in DynamoDB/S3. Record the Sheets failure as `PARTIAL_SUCCESS`.
- A report with no approved `KEEP` rows must not be sent to subscribers.
- Remove Google Sheet writes by disabling the integration flag and redeploying. DynamoDB/S3 evidence must remain intact.
- Do not delete the existing DynamoDB table or S3 bucket during this sequence.

## Definition of done for the manual-first v1

- Daily collection runs without Bedrock or subscriber delivery.
- Evidence appears in a private Google Sheet with stable item IDs and review fields.
- The same item does not create duplicate rows on a repeat collection.
- Operator decisions survive another collection run.
- A weekly draft can be generated from `KEEP` rows only.
- Draft delivery goes to the operator only.
- Subscriber approval is manual.
- Report scheduling remains disabled until the review gate is implemented and tested.
