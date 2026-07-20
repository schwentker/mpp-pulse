# Stage 6: Tier-1 source fixtures and precision gates

This is the Stage 6 checkpoint. It defines the fixtures, acceptance thresholds,
and false-positive controls required before any new collector is enabled.

## Non-negotiable eligibility gate

A candidate is eligible only when all of the following are present:

1. A canonical source URL on an allowlisted first-party host.
2. A parseable publication, update, merge, or release date inside the collection window.
3. A supporting excerpt containing a specific entity match: `MPP`, `Machine Payments
   Protocol`, `x402`, `PaymentAuth`, `Payment`, `HTTP 402`,
   `draft-ryan-httpauth-payment`, or an allowlisted repository name.
4. An explicit source classification: `inventory`, `implementation_activity`, `news`,
   or `unverified_community`.
5. A confidence value. Primary-source releases, merged pull requests, and new IETF
   draft revisions may be `high`; a first-party blog post without a directly stated
   launch is `medium`.

Reject a result when it has only a bare `402`, generic agent-payment wording, an
unmatched vendor name, an undated page, a noncanonical URL, or third-party commentary
without first-party corroboration. Deduplicate by normalized canonical URL and stable
source identity before scoring. A release, its tag, and its release note are one event.

## Source fixtures

| Source | Canonical surface | Accept fixture | Reject fixture | Classification |
| --- | --- | --- | --- | --- |
| IETF Payment Auth | `https://datatracker.ietf.org/doc/draft-ryan-httpauth-payment/` | A new numbered draft revision or changed Datatracker update date with the draft identifier and author list | A different Internet-Draft that merely says `402` or cites the draft in references | `implementation_activity` / `news` only when the primary record states a material revision |
| GitHub release | `https://github.com/{owner}/{repo}/releases.atom` | A non-draft release for an allowlisted MPP or PaymentAuth repository with `published_at`, tag, URL, and release notes | A release from an unrelated repository, or a draft/prerelease unless specifically requested | `news` |
| GitHub tag | `https://github.com/{owner}/{repo}/tags.atom` | A newly observed tag in an allowlisted repository, only if no matching release event exists | A tag that duplicates an already captured release | `implementation_activity` |
| Merged GitHub PR | GitHub REST pull-request endpoint for an allowlisted repository | `state=closed`, non-null `merged_at`, merge time inside window, and title/body naming a scoped protocol entity or repository component | Closed-but-unmerged PR, Actions-generated update, dependency noise without scoped payment evidence | `implementation_activity` |
| Official blogs | Verified feed URL on the Tempo, Coinbase/CDP/x402, Cloudflare, Circle, or paymentauth.org first-party domain | Dated post with an explicit protocol/entity match in title or excerpt | Generic AI, stablecoin, API, or HTTP content without a protocol/entity match | `news` |

`paymentauth.org` does not receive a feed URL until one is verified from the site itself.
Likewise, Tempo, Coinbase/CDP/x402, Cloudflare, and Circle are host allowlists first;
their concrete feed endpoints must be verified before configuration. Do not guess feeds
or use search engines to fill a missing source.

The IETF fixture is deliberately strict: Datatracker currently identifies
`draft-ryan-httpauth-payment` as an active individual Internet-Draft, not an IETF
standard. See the [Datatracker record](https://datatracker.ietf.org/doc/draft-ryan-httpauth-payment/).

## Fixture data

Machine-readable cases are in
[`tests/fixtures/tier1-source-fixtures.json`](../tests/fixtures/tier1-source-fixtures.json).
They are offline contracts, not live-network tests.

## Acceptance thresholds before activation

- At least 20 hand-labeled cases per source family before the source is enabled.
- `>= 90%` precision on each source family; no false positive for a bare `402` or
  generic agent-payment term.
- `100%` of accepted cases contain canonical URL, date, excerpt, entity match,
  confidence, and classification.
- `100%` of release/tag duplicate pairs collapse to one event.
- `100%` of closed-but-unmerged pull requests are rejected.
- No source may make a failed network request fatal to the whole collection run.

## Precision risks to resolve during implementation

- **IETF:** revised drafts are work in progress; never describe them as a ratified
  standard or deployed integration.
- **GitHub:** merged PRs show implementation movement, not necessarily a shipped
  feature. Releases and tags can duplicate the same event.
- **Blogs:** vendor announcements may discuss adjacent payments or AI without an MPP,
  x402, PaymentAuth, HTTP 402, draft, or named-repository connection.
- **Feeds:** a page's publication date can be absent, modified later, or outside the
  report window. Without a reliable date, reject it.

## Proposed implementation order after approval

1. IETF draft revision detector and its fixtures.
2. GitHub releases, tags, and merged pull requests for the existing allowlisted repos.
3. One verified official feed at a time, beginning with Tempo.
4. Add each source to the registry only after its offline fixture suite passes.
