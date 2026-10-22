# Phase 4: weighted acceptance from offline evidence

This phase adds a separate, offline acceptance evaluator. Normal migration
still generates contracts, artifacts and static readiness without claiming
execution or semantic acceptance. No provider, target connection, deployment
or generated workload is invoked by this evaluator.

## Reviewed policy and supplied evidence

Each contract obligation is identified by `(contract_id, behavior_id)`.
The policy must cover every obligation exactly once with a positive finite
weight and explicit Boolean criticality. Drafts leave these values null.
Missing assessments block acceptance and leave coverage unknown; no default
business importance is invented. Contract documents and their hashes remain
unchanged. Unsupported, missing and unverified behaviors remain in the total
weight. Unsupported behaviors never earn credit.

Weighted semantic coverage is `100 × verified weight / total weight`, with
no rounding before comparison. The configurable threshold must be between
80 and 100 inclusive. Every critical behavior must have passing semantic,
execution and data-equivalence evidence, even if overall coverage is 99%.
Structural validation counts remain distinct from these three weighted
coverage dimensions. Missing execution/data checks retain `NOT_RUN` status.

Mandatory gates are `source_review`, `execution`, and `data_equivalence`.
Policies may add gates but cannot remove these. Source review must establish
that the obligations represent the exported source behavior completely,
including control flow, side effects and critical rules. Execution and data
reconciliation reports must document controlled testing of the supplied
artifacts. A gate PASS does not override failed checks or missing critical
behavior evidence. Execution and data-equivalence each need at least one
passing behavior check as well as their package gate. Existing static blockers and contract unresolved items
cannot be waived with an attestation. Resolve source gaps and regenerate
artifacts/contracts before re-reviewing them.

Every passing check/gate references one or more local report files with a
SHA-256 digest, a specific locator, a reviewer and the method used. Reports
must remain within the evidence document's directory (including symlink
resolution). Duplicate/unknown behavior identities, duplicate gates, duplicate
JSON keys, non-finite numbers and unsupported schema versions are rejected.
Evidence binds to a digest of the reviewed policy and an exact inventory of
the current contracts, plan, validation records, generated artifact files,
notebooks, jobs and DAB configuration. Changed/missing/added artifacts or
changed source input files prevent evidence from earning credit. The loader
also verifies contracts against plan steps, reruns notebook static checks and
compares job task selection/dependencies with the plan.

**Trust boundary:** report contents, check outcomes and reviewer identities
are operator-supplied attestations. Hashes establish byte integrity; this
module does not authenticate reviewers, establish that a report is truthful,
run a test, or independently prove semantic equivalence. An `ACCEPTED` result
means the supplied reviewed evidence passes this policy. It grants no
permission to deploy or execute a workload. These distinctions are retained
in the machine-readable result.

## Use

From the repository with its Python environment active, first generate a
migration normally, then prepare drafts in a new directory:

```bash
python -m ssis_migration.validator.acceptance \
  --package-dir /path/to/output/PackageName \
  --prepare /path/to/review
```

Review `policy.json`, assigning weights and criticality from actual business
requirements. Supply the controlled test/comparison reports under the review
directory. In `evidence.json`, add per-behavior checks for `semantic`,
`execution`, and `data_equivalence`, and complete the mandatory gates.
For example, one check has this shape (use actual identities and hashes):

```json
{
  "contract_id": "contract-from-bundle",
  "behavior_id": "transformation-0",
  "dimension": "semantic",
  "status": "PASS",
  "reports": [{
    "path": "reports/comparison.json",
    "sha256": "actual-64-character-sha256",
    "locator": "cases/customer-deduplication",
    "reviewer": "responsible-reviewer",
    "method": "Compared exported source behavior with controlled target results"
  }]
}
```

Other statuses are `FAIL`, `NOT_RUN`, `UNSUPPORTED` and `UNVERIFIED`. A known
FAIL blocks acceptance. A noncritical unverified gap can remain within the
allowed semantic threshold, but never receives credit. Reviewers must not
mark a result PASS just because code was generated or compiled.

After approving the policy contents, bind the evidence to that exact policy
without changing its captured artifact inventory:

```python
import json
from pathlib import Path
from ssis_migration.validator.acceptance import document_digest

review = Path('/path/to/review')
policy = json.loads((review / 'policy.json').read_text())
evidence = json.loads((review / 'evidence.json').read_text())
evidence['policy_digest'] = document_digest(policy)
(review / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
```

Evaluate into a new result file:

```bash
python -m ssis_migration.validator.acceptance \
  --package-dir /path/to/output/PackageName \
  --policy /path/to/review/policy.json \
  --evidence /path/to/review/evidence.json \
  --output /path/to/review/acceptance.json
```

Exit codes are 0 for accepted, 1 for evaluated but blocked, and 2 for malformed
inputs or file errors. Existing drafts/results are never overwritten. The
new acceptance result includes blockers, separate coverage dimensions, every
behavior's assessment/result, gate results and input digests. Generation's original readiness and manifest stay
as historical static results; this command does not rewrite them or change
the legacy generation statuses.

## Compatibility and remaining work

No existing migration/provider/IR interface changes. Two packaged version-1
schemas and a new evaluator/CLI are additive. Existing SSIS generation remains
available without policy or evidence files.

This phase provides evidence accounting and acceptance gates, not test runners,
reconciliation engines, automatic source semantic verification or the four
remaining platform adapters. Phase-3 bridge inference remains unverified until
reviewed evidence is supplied. Existing sample migrations with unresolved
source dependencies remain blocked. The next implementation phase can add
source adapter semantics and controlled validation producers incrementally.

## Changed files

| File | Purpose |
| --- | --- |
| `src/ssis_migration/validator/acceptance.py` | Offline package loader, evidence/policy binding, weighted evaluator, detailed behavior results and CLI. |
| `src/ssis_migration/schemas/acceptance_policy.v1.json` | Explicit coverage threshold, complete behavior assessments and additional gates. |
| `src/ssis_migration/schemas/acceptance_evidence.v1.json` | Hashed report references, separate behavior checks and package gates. |
| `src/ssis_migration/schemas/__init__.py` | Register the two bundled schemas for offline validation. |
| `tests/validator/test_acceptance.py` | Threshold/precision boundaries, critical vetoes, missing/invalid evidence, stale bindings, CLI compatibility and no-execution checks. |
| `docs/phase4-weighted-acceptance.md` | Operating instructions, evidence trust boundary and remaining work. |

## Verification

- Full suite: **382 passed, 4 live integration tests skipped**.
- 65 acceptance cases cover the new evaluator, including a below-80 value
  that floating-point reporting rounds to 80; the exact comparison blocks it.
- Built wheel imported outside the checkout; both new bundled schemas and
  the evaluator loaded successfully.
- Sample smoke: all 130 contract behaviors retained; acceptance `BLOCKED`,
  semantic coverage unknown, execution `NOT_RUN`, mandatory gates unpassed.
- No target workload, deployment or live AI request was executed.
