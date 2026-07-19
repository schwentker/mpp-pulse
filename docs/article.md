# Weekend Agent Challenge: MPP Pulse, an Always-On Payments Intelligence Agent

Machine payments are moving quickly across protocols, service directories, developer tooling, and payment networks. Keeping up is difficult because the useful evidence is scattered across official documentation, company blogs, and source-code repositories. A generic news summary does not solve that problem: it tends to repeat popular commentary instead of identifying concrete changes.

I built **MPP Pulse**, a small always-on intelligence agent focused on the Machine Payments Protocol (MPP) and the Tempo ecosystem. Every morning, without waiting for a button click, it checks a short list of primary sources, records what is new, asks Amazon Nova to prepare a cited briefing, and saves a readable HTML report. The result is waiting in Amazon S3 when I begin work.

## Vision and what the agent does

MPP Pulse answers one practical question: “What changed in machine payments since the previous run, and why might it matter?”

The agent watches three deliberately narrow sources. First, it retrieves the public MPP services catalog to detect additions or changed service records. Second, it checks the official Tempo blog index for ecosystem announcements. Third, it reviews recent commits in the MPP GitHub repository for implementation movement. Each collector runs independently, so one unavailable source does not prevent the other sources from producing a report.

The workflow normalizes URLs, assigns stable content identifiers, removes duplicates, and gives each item a deterministic importance score. Official sources receive the strongest base weight, with additional points for terms associated with releases, protocol changes, SDKs, settlement, security, and integrations. Only newly observed evidence is sent to the language model.

Amazon Nova receives a bounded JSON evidence packet. The prompt instructs the model to use only that evidence, cite claims with source labels such as `[S1]`, distinguish interpretation from fact, and avoid describing a proposal as a shipped feature. If model invocation fails, the Lambda still creates a deterministic report with ranked source links. This fallback keeps the agent useful and makes failure behavior easy to demonstrate.

## How I built it

I optimized for the smallest complete autonomous system. The entire application runs in one Python Lambda function and uses Python’s standard library for external HTTP requests. This avoids extra infrastructure and reduces deployment size.

Idempotency was important because scheduled events may be delivered more than once. Before a normal daily run, the Lambda performs a conditional DynamoDB write for that date. If the lock already exists, a repeated invocation exits safely. Evidence records also use conditional writes, so an unchanged catalog service, blog link, or commit is not inserted twice.

The most important scope decision was to avoid broad social-media ingestion. X and LinkedIn adapters would require more credentials, permissions, and compliance work without improving the core challenge demonstration. They remain possible future additions. Reddit is also omitted from the first deployment because the three primary sources are sufficient to demonstrate autonomous collection, reasoning, persistence, and reporting.

## AWS services and architecture

Amazon EventBridge Scheduler triggers the Lambda every day at 6:00 AM in the `America/Los_Angeles` timezone. The Lambda retrieves source data, writes operational state and evidence to Amazon DynamoDB, invokes Amazon Bedrock with an Amazon Nova model, and saves a private HTML report in Amazon S3. Amazon CloudWatch receives structured logs and has a basic Lambda error alarm.

The project is deployed with AWS SAM. The template creates one Lambda, one DynamoDB on-demand table, one private encrypted S3 bucket, one schedule, and one alarm. The function has reserved concurrency of one, a 90-second timeout, bounded source requests, and a small Bedrock output limit. S3 reports expire after 30 days. These controls keep the demonstration inexpensive and prevent accidental continuous execution.

## What I learned

The main lesson was that an agent’s autonomy is easier to trust when collection and persistence are deterministic. The model is valuable for synthesis, but it should not decide what evidence exists. Stable identifiers, conditional writes, source-specific failure handling, and a fallback report make the system easier to inspect.

I also learned that a small number of authoritative sources creates a better first product than a large set of noisy integrations. MPP Pulse can expand later, but the weekend version already provides an observable loop: wake up, inspect primary sources, detect new evidence, summarize it, and leave a report behind.

The repository includes the SAM template, application code, tests, architecture diagram, deployment guide, rollback instructions, and evidence checklist.

Repository: **https://github.com/schwentker/mpp-pulse**

Tag: **#agents**
