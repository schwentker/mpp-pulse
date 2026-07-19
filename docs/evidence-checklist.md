# Challenge evidence checklist

Capture these after deployment:

- [x] CloudFormation stack in `UPDATE_COMPLETE`.
- [x] EventBridge Scheduler detail page showing the weekly schedule and timezone.
- [x] CloudWatch log showing an invocation with `trigger: demo-schedule`.
- [x] Lambda log line containing `status`, `new_items`, and `report_key`.
- [x] DynamoDB contains run and evidence records.
- [x] S3 contains `reports/YYYY-MM-DD/...html` objects.
- [x] Opened HTML report using a short-lived presigned URL.
- [ ] GitHub repository homepage with README and architecture diagram.

Verified autonomous run:

- Time: `2026-07-19T06:54:44Z`
- Status: `SUCCEEDED`
- Trigger: `demo-schedule`
- Collected: `121`
- New duplicate inserts: `0`
- Collector errors: `0`
- Temporary evidence schedule: automatically deleted after completion

Do not capture account IDs, access keys, email addresses, or other secrets.

Recommended filenames:

1. `01-stack-complete.png`
2. `02-eventbridge-schedule.png`
3. `03-autonomous-invocation.png`
4. `04-dynamodb-run.png`
5. `05-html-report.png`
6. `06-github-repository.png`
