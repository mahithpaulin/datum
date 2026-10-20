# Commercial validation plan

This is a plan to test whether DATUM is worth commercializing. It is not evidence that a
market exists. No customer interviews, paid pilots, or revenue have been recorded yet.

## Working hypothesis

Teams that prepare training or evaluation data locally need a repeatable build artifact more
than another annotation UI. They will pay for a tool that reduces silent data-preparation
failures, makes dataset provenance auditable, and fits into existing scripts and CI without
sending raw data to a vendor.

The hypothesis fails if target users are satisfied with ad-hoc scripts, cannot articulate a
cost for bad dataset builds, or require managed infrastructure DATUM does not provide.

## Initial customer segments

| Segment | Trigger | Current alternative | Fit |
| --- | --- | --- | --- |
| Small applied AI teams | Fine-tuning on proprietary text; inconsistent notebooks | Ad-hoc Python scripts | Strong: local, schema-based, auditable |
| Data/ML platform teams | Recurring dataset releases; audit requests | Internal pipeline glue | Medium: needs integration and scale evidence |
| Regulated or air-gapped teams | Cannot send raw data to SaaS | Manual exports and spreadsheets | Strong: local/offline, no network requirement |
| Consultancies and agencies | Repeatable client data delivery | Per-project scripts | Medium: strong artifact story, price-sensitive |
| Research groups | Reproducible corpora for papers | Custom scripts and READMEs | Medium: reproducibility value, weak budgets |

Non-targets for the first version: teams needing OCR, web-scale crawling, managed labeling,
real-time ingestion, or a full feature store.

## Value proposition to test

1. **Fewer silent failures.** Every input and candidate has an accounted outcome.
2. **Reproducible releases.** Same source bytes plus configuration produce the same dataset
   bytes, with a build ID and output identity.
3. **Independent verification.** A verifier checks the bytes after the build, not just the
   build log.
4. **Local control.** No upload, no required service, standard-library core.
5. **Faster review.** Quality report and rejection log make data decisions reviewable.

Each claim should be tested with a concrete before/after workflow, not a feature demo.

## Pricing hypotheses

These are hypotheses to validate, not published prices.

| Model | Hypothesis | Risk |
| --- | --- | --- |
| Free CLI, paid support | Individuals adopt; teams pay for response times and review | Support does not scale; weak expansion |
| Per-seat commercial license | Analysts and engineers pay for audited builds | Buyers may expect a hosted product |
| Per-project license | Agencies pay per client data delivery | Hard to meter and enforce |
| Open-core | Core stays free; paid policy packs, connectors, or audit integrations | Requires a real open-source strategy and community |
| Managed private runner | Teams pay for scheduled builds and retention | Becomes infrastructure business, not compiler business |

Recommended first experiment: a paid pilot for a fixed-scope dataset build, priced as a
services engagement with a commercial license option. This tests willingness to pay without
inventing a pricing page.

## Validation experiments

### 1. Problem interviews

- Recruit 15–20 practitioners who have shipped a fine-tuned model or a dataset release in the
  last year.
- Ask for the last failed data build, how it was detected, who was affected, and what it cost
  in time or trust. Do not pitch first.
- Evidence to capture: current tooling, review process, artifact requirements, procurement
  path, and budget owner.

Success signal: at least five interviewees describe a recent, costly, repeatable failure that
a verified local build would have caught.

### 2. Concierge builds

- Run DATUM on five real corpora under NDA, with the owner present.
- Measure setup time, rejected-record surprises, unsupported-format blockers, and whether the
  manifest/quality report changes a decision.
- Deliver a verified build plus a written review of failures and gaps.

Success signal: at least three owners say they would use the build in a real release, and two
will pay for a repeat engagement.

### 3. Reproducibility audit

- Ask a team to rebuild a prior dataset from the manifest and configuration.
- Verify byte-level identity where possible and explain every difference.
- Record whether the audit exposed undocumented steps.

Success signal: the audit finds a concrete gap in the team's current process and the team
asks for a repeatable service.

### 4. Workflow integration trial

- Integrate `datum build` and `datum verify` into one team's CI or release script.
- Require a quality-failed build to block publication.
- Track time saved in review and incidents avoided over four weeks.

Success signal: the team keeps the gate enabled after the trial and asks for more formats or
policy controls.

### 5. Paid pilot

- Scope one corpus, one schema, one acceptance report, and one support window.
- Charge a small fixed fee. Do not discount to zero; free pilots do not test willingness to
  pay.
- Define acceptance before work starts: verified build, accounted rejections, reproducible
  output identity, and a written limitations report.

Success signal: payment clears, acceptance is met, and the buyer discusses a second corpus or
an annual license.

## Buying criteria to probe

- Time to a verified dataset
- Trust in counts and provenance
- Reproducibility across machines and months
- Handling of unsupported formats and malformed records
- Security and data residency
- Integration with Python, CI, and existing storage
- Support responsiveness and roadmap influence
- Price relative to engineering time, not relative to storage costs

## Packaging options to explore

- **Compiler license:** CLI plus commercial support, per organization.
- **Audit pack:** independent verification report for a dataset release.
- **Custom schema and adapter pack:** paid implementation for a customer's formats.
- **Private runner:** scheduled builds with retention, only if customers demand it.
- **Policy pack:** organization-specific quality gates, approvals, and retention rules.

Do not build a hosted platform before pilots show demand and the operational cost is
understood.

## Success metrics

| Stage | Metric | Target to continue |
| --- | --- | --- |
| Discovery | Interviews completed | 15 |
| Discovery | Costly failure stories | 5 |
| Concierge | Real corpora built | 5 |
| Concierge | Buyers willing to pay | 2 |
| Pilot | Paid pilots started | 2 |
| Pilot | Pilots accepted | 1 |
| Early revenue | Repeat engagement or license | 1 |
| Product | Rebuild identity preserved | 100% of pilot builds |

Track setup hours, unsupported-format rate, rejection rate, review time, and support hours per
build. A high rejection rate is not automatically bad; unexplained rejection is.

## Risks

- **Market risk:** teams tolerate scripts and will not pay for provenance.
- **Scope risk:** every corpus needs a new adapter, turning the product into services.
- **Trust risk:** one silent data error destroys the core promise.
- **Performance risk:** the Python compile stage may be too slow for large corpora.
- **Compliance risk:** handling regulated data creates obligations the project cannot meet.
- **Competition risk:** data platforms bundle basic validation and provenance.
- **Licensing risk:** proprietary terms may block adoption in some organizations.

Mitigations: narrow the first ICP, keep the verifier independent, publish honest benchmarks,
refuse out-of-scope data, and price services explicitly while the product is immature.

## 90-day plan

Weeks 1–2: finish documentation, sample artifacts, and a reproducible test suite; define the
pilot scope and acceptance report; prepare interview script and consent terms.

Weeks 3–6: run 15 interviews; build three concierge corpora; record every unsupported format
and manual step; fix only blockers that recur across customers.

Weeks 7–10: run two paid pilots; integrate verification into one CI workflow; measure setup
and review time; write a limitations report for each pilot.

Weeks 11–12: decide go/no-go. Continue only if at least one paid pilot is accepted and a
second customer commits to a repeat engagement. Otherwise narrow the segment or stop.

## Ethical and legal boundaries

- Do not claim dataset quality, legal compliance, or model improvement without evidence.
- Do not process data without the owner's authorization.
- Do not store customer corpora or secrets in the repository, benchmarks, or memory.
- Keep provenance useful without becoming a surveillance record.
- Treat deletion, retention, and access requests as first-class requirements in any paid
  deployment.
