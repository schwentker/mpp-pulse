# Weekend Agent Challenge: MPP Pulse, an Always-On Payments Intelligence Agent

Tag: #agents

![MPP Pulse architecture](screenshots/06-architecture-diagram.png)

Machine payments are becoming one of the most interesting parts of the agent economy. The idea is simple to say but hard to track: software agents, APIs, and services need a way to pay each other programmatically, often at very small amounts, without stopping for a human checkout flow. The Machine Payments Protocol (MPP) and Tempo ecosystem are early signals of that shift. But the actual evidence is scattered across service catalogs, company blogs, and GitHub repositories.

That scatteredness is the problem I built for. I wanted a small agent that would wake up before I start work, inspect the most important machine-payments sources, separate real movement from ambient noise, and leave a useful briefing behind. Not a button-driven chatbot. Not a generic news digest. A narrow, always-on research assistant for one fast-moving technical domain.

The result is **MPP Pulse**, an AWS serverless agent that runs on a schedule and produces a daily machine-payments intelligence report.

## Vision and What the Agent Does

MPP Pulse answers one practical question:

> What changed in machine payments since the last run, and why might it matter?

The agent is triggered automatically by Amazon EventBridge Scheduler at 6:00 AM in the `America/Los_Angeles` timezone. Once triggered, it runs without human input. It collects evidence from a deliberately small set of high-signal sources: the public MPP services catalog, the Tempo blog, and recent GitHub activity related to MPP. I intentionally kept the first version focused on primary or near-primary sources because they are easier to trust and easier to cite.

![Lambda function in AWS Console](screenshots/05-lambda-function.png)

Each collector produces normalized evidence items with a title, URL, source type, timestamp, content fingerprint, and short summary text. The Lambda then deduplicates the evidence, calculates deterministic importance scores, and saves operational state in DynamoDB. The score is not magic. Official protocol or company sources receive a higher base weight, and items can gain points for terms related to releases, SDKs, settlement, integrations, security, subscriptions, and protocol changes.

After collection and scoring, MPP Pulse builds a bounded evidence packet and sends it to Amazon Bedrock using an Amazon Nova model. The model is instructed to summarize only the provided evidence, cite claims with source labels, separate facts from interpretation, and avoid overstating GitHub activity as shipped product changes. The final result is saved as a private HTML report in Amazon S3.

![Generated MPP Pulse report](screenshots/03-html-report.png)

That reporting loop is the whole point: I do not need to remember to run the tool. When the schedule fires, the agent collects, reasons, persists, and leaves the report ready.

## How I Built It

My main development decision was to keep the system small enough to finish and verify in a weekend. I used AWS SAM, one Python Lambda function, one DynamoDB table, one private S3 bucket, Amazon Bedrock, and EventBridge Scheduler. The design goal was not to build the final version of a market-intelligence platform. It was to build the smallest complete autonomous agent that satisfies the challenge and remains useful after the challenge.

The Lambda uses Python standard-library HTTP requests so the deployment package stays simple. The code is organized around source collectors, normalization, scoring, persistence, summarization, and report rendering, but it all ships as one function. Each collector is isolated so a failure in one source does not fail the entire run. If the Tempo blog is temporarily unavailable, the catalog and GitHub collectors can still produce evidence.

Idempotency was the most important engineering detail. Scheduled systems can retry. Manual demos can also be run more than once. To prevent duplicate reports and duplicate evidence, the Lambda writes a daily lock record to DynamoDB with a conditional expression. Evidence items also use stable identities and content fingerprints. If the same catalog service, blog link, or commit appears again unchanged, DynamoDB rejects the duplicate insert and the run continues safely.

I also added a deterministic fallback report. If Bedrock is unavailable, the system still writes a ranked evidence report with source links. That makes the agent easier to operate because the collection layer is useful even when the summarization layer has a temporary issue.

One challenge was deciding what not to build. It would be tempting to add X, LinkedIn, Reddit, Hacker News, and broad web search immediately. I documented those as future adapters, but I left them out of the weekend-critical path. Social integrations introduce authentication, rate limits, compliance questions, and a lot of noise. For this challenge, MPP services, Tempo posts, and GitHub activity are enough to prove the autonomous loop.

## AWS Services Used and Architecture Overview

The deployed architecture is intentionally compact:

- **Amazon EventBridge Scheduler** wakes the agent every morning at 6:00 AM.
- **AWS Lambda** runs the collector, deduplication, scoring, Bedrock call, and report rendering.
- **Amazon DynamoDB** stores the daily lock, run records, and evidence records.
- **Amazon Bedrock with Amazon Nova** turns the ranked evidence packet into a cited daily brief.
- **Amazon S3** stores the private HTML report artifact.
- **Amazon CloudWatch** stores logs and provides a basic Lambda error alarm.
- **AWS SAM** defines and deploys the infrastructure.

The flow is:

1. EventBridge Scheduler invokes the Lambda.
2. Lambda checks the DynamoDB daily lock.
3. Lambda collects from MPP, Tempo, and GitHub.
4. Lambda deduplicates and scores evidence.
5. Lambda stores run and evidence records in DynamoDB.
6. Lambda asks Bedrock/Nova for a cited summary.
7. Lambda writes the HTML report to S3.
8. CloudWatch captures logs and operational evidence.

![Autonomous scheduled invocation evidence](screenshots/04-autonomous-invocation.png)

For cost control, the table uses on-demand billing, the Lambda has reserved concurrency of one, the function timeout is 90 seconds, the Bedrock output is capped, and S3 report artifacts expire after 30 days. The daily schedule is also easy to disable if I want to pause the agent after the challenge.

## What I Learned

The biggest lesson was that autonomy is more convincing when the boring parts are solid. A beautiful summary is useful, but the trust comes from the schedule, idempotency lock, stable evidence identifiers, source isolation, private report storage, and observable logs. Those are the details that make the agent feel like a reliable worker rather than a demo script.

I also learned that a narrow source strategy is a strength. The first version of MPP Pulse is not trying to understand the entire internet. It is watching a few places where real machine-payment development is likely to appear first. That makes the report shorter, easier to verify, and more relevant.

Finally, I came away with a clearer pattern for future agents: let deterministic code collect and rank evidence, then let the model synthesize. The model should not be the fact database. It should be the analyst that reads a carefully prepared evidence packet.

The next version of MPP Pulse could add Reddit, X, LinkedIn, more company blogs, release monitoring, and an API endpoint for retrieving the latest brief. Eventually, it would be fitting to expose the brief through MPP itself: an intelligence product about machine payments that other agents can pay to access.

## Link to App or Repo

Public GitHub repo with source code:

https://github.com/schwentker/mpp-pulse

The repo includes the AWS SAM template, Lambda source code, tests, deployment instructions, rollback notes, cost controls, screenshots, architecture diagram, and this article draft.
