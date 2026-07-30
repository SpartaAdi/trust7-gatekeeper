# Trust7 Gatekeeper — rubric checklist (all 45 checks)

A read-only export of `rubric/rubric.json`, which is what the classify and
evaluate stages actually read. Generated from that file — if the two ever
disagree, the JSON is authoritative and this export is stale.

**This is an export, not a source.** Editing this file changes nothing. To change
a check, edit `rubric/rubric.json`.

## How to read it

Each check gets one of four verdicts:

| Verdict | Meaning |
| --- | --- |
| `pass` | The design demonstrably satisfies the check. |
| `partial` | The design partly addresses it, or states an intent without the mechanism. |
| `fail` | The design does not address it, or addresses it in a way that defeats its purpose. |
| `not_applicable` | The check cannot apply to this design's shape. |

Two rules that decide most borderline calls:

- **Silence is not a pass.** If the design does not establish something a check
  requires, that is `fail` or `partial` — never `pass`, and never
  `not_applicable`.
- **`not_applicable` needs the design to make the check inapplicable, not merely
  unmentioned.** "This system has no AI component" makes `tf_hallucination_control`
  inapplicable. "This system does not discuss encryption" does *not* make
  `sec_encryption_at_rest` inapplicable — it makes it `fail`.

`Severity` is the check's starting severity. A reviewer may raise it where this
design's specifics make the gap more dangerous (sensitive data, external
exposure, irreversibility) or lower it where they make it less so.

Scoring weights severity: high 3, medium 2, low 1. `not_applicable` checks are
excluded from the denominator entirely rather than counted as failures, and a
pillar whose checks are *all* `not_applicable` is excluded from its framework's
average rather than scored as zero.

## Watch out for these

Several TRUST-7 checks read as AI-specific but are not, and several read as
general but are AI-scoped. The ones that catch people:

- `ss_data_residency` — **not** AI-specific. It applies to any design holding
  regulated data.
- `tf_privacy` — scoped to PII flowing through **AI components**. General PII
  protection is `sec_encryption_at_rest` / `sec_least_privilege`.
- `ue_cost_per_unit` — cost per **AI call**. General cost visibility is
  `cost_visibility`.
- `gov_audit_trail` — logging of **AI decisions**. General security logging is
  `sec_audit_logging`.
- `rr_incident_response_ai` — an incident path for **AI failures**. General
  incident response is `oe_incident_response`.

## Contents

| Framework | Pillars | Checks |
| --- | --- | --- |
| WAF-6 — AWS Well-Architected Framework | 6 | 26 |
| TRUST-7 — Minfy TRUST-7 Framework | 7 | 19 |
| **Total** | **13** | **45** |

---

# WAF-6 — AWS Well-Architected Framework

26 checks across 6 pillars.

## Operational Excellence

`operational_excellence` · 4 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `oe_iac` | medium | Infrastructure and deployments are defined as code / automated, not manual console changes. |
| `oe_observability` | high | Monitoring, logging, and alerting are defined for the workload's key operational signals. |
| `oe_incident_response` | medium | An incident response process or runbook is referenced for operational failures. |
| `oe_change_mgmt` | medium | Changes can be rolled back safely; deployment approach limits blast radius of a bad change. |

## Security

`security` · 7 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `sec_least_privilege` | high | IAM roles/permissions follow least privilege rather than broad/wildcard access. |
| `sec_encryption_at_rest` | high | Sensitive or PII-bearing data stores have encryption at rest specified. |
| `sec_encryption_transit` | high | Data in transit is encrypted (TLS) between components, especially over public networks. |
| `sec_secrets_mgmt` | high | Credentials/API keys/secrets are managed via a secrets store, not hardcoded or embedded in config. |
| `sec_network_perimeter` | medium | Network boundaries/segmentation are defined; public-facing components are minimized and fronted appropriately. |
| `sec_identity_auth` | high | Authentication/authorization (SSO, MFA, RBAC) is defined for user- and system-facing access. |
| `sec_audit_logging` | medium | Security-relevant actions and access are logged in an auditable, tamper-resistant way. |

## Reliability

`reliability` · 4 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `rel_redundancy` | high | Critical components have redundancy (multi-AZ or equivalent) rather than a single point of failure. |
| `rel_dr_plan` | medium | A disaster recovery approach with defined RTO/RPO is present for critical data/workloads. |
| `rel_failure_isolation` | medium | Dependent-service failures are isolated (retries, timeouts, circuit breakers, dead-letter queues) rather than cascading. |
| `rel_backup` | medium | A backup strategy exists for stateful data with a defined restore process. |

## Performance Efficiency

`performance_efficiency` · 4 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `perf_right_sizing` | low | Compute/storage choices match the actual workload pattern (e.g., serverless for spiky load) rather than over-provisioning. |
| `perf_caching` | low | Caching is used where repeated reads or expensive computations would otherwise recur. |
| `perf_data_store_fit` | medium | The database/storage type matches its access pattern (e.g., not using a relational store for pure key-value lookups at scale). |
| `perf_global_delivery` | low | Content/data delivery accounts for geographic distribution of users where relevant (CDN or regional deployment). |

## Cost Optimization

`cost_optimization` · 4 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `cost_visibility` | medium | Cost tracking/tagging/budgets are defined so spend is attributable and boundable. |
| `cost_elasticity` | medium | Resources scale down/to zero with demand rather than running at fixed capacity for variable load. |
| `cost_storage_lifecycle` | low | Storage has a lifecycle/retention policy rather than indefinite accumulation at full-cost tiers. |
| `cost_provisioning_model` | low | Provisioning model (on-demand vs reserved/provisioned) matches the workload's actual usage predictability. |

## Sustainability

`sustainability` · 3 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `sus_managed_services` | low | Managed/serverless services are used where feasible to improve utilization over self-managed idle infrastructure. |
| `sus_data_minimization` | low | Data retention/storage footprint is minimized to what's needed rather than retained indefinitely by default. |
| `sus_region_awareness` | low | Region/deployment choice shows awareness of energy/carbon considerations where it doesn't conflict with other constraints. |

---

# TRUST-7 — Minfy TRUST-7 Framework

19 checks across 7 pillars.

## Trust foundations

`trust_foundations` · 4 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `tf_explainability` | high | AI-driven decisions that affect people can be explained at a level appropriate to the audience. |
| `tf_fairness` | high | AI outputs are evaluated for unjustified variation across populations/segments where the use case involves consequential decisions. |
| `tf_hallucination_control` | high | LLM outputs are grounded (RAG, validation, citation) rather than relying on unverified generation for factual claims. |
| `tf_privacy` | high | PII flowing through AI components is identified and protected (minimization, masking, or encryption). |

## Risk and resilience

`risk_resilience` · 3 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `rr_ai_threat_model` | high | AI-specific threats (prompt injection, data poisoning, model extraction) are considered, not just traditional infra security. |
| `rr_incident_response_ai` | medium | An incident response path exists specifically for AI failures (bad output, hallucination, misuse), not just general system incidents. |
| `rr_validation_before_prod` | medium | AI models/outputs are validated (testing, red-teaming, or human review) before being relied upon in production. |

## Unit economics

`unit_economics` · 3 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `ue_cost_per_unit` | medium | Cost per AI call/decision is knowable or estimable, not opaque. |
| `ue_model_routing` | low | Cheaper/smaller models are used for simple tasks, reserving expensive models for tasks that need them. |
| `ue_caching_ai` | low | Repeated or semantically similar AI queries are cached rather than always re-invoking the model. |

## Sovereignty and supply chain

`sovereignty_supply_chain` · 3 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `ss_data_residency` | high | Data residency/location constraints are explicit and enforced by design, not left implicit. |
| `ss_provider_dependency` | medium | Foundation model/vendor dependency is acknowledged with some consideration of switching cost or exit path. |
| `ss_abstraction` | low | AI provider calls are abstracted behind an internal interface rather than tightly coupled to one vendor's SDK throughout the codebase. |

## Talent and adoption

`talent_adoption` · 2 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `ta_human_in_loop` | medium | Consequential AI decisions retain a human review/override point rather than being fully autonomous. |
| `ta_user_training` | low | End users/operators have a defined path to understand and correctly use the AI system's outputs. |

## Sustainability (AI-specific)

`sustainability_ai` · 1 check

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `sai_inference_efficiency` | low | Design choices (model size, caching, batching) that reduce cost also incidentally reduce AI inference energy footprint. |

## AI governance

`ai_governance` · 3 checks

| Check ID | Severity | What to judge |
| --- | --- | --- |
| `gov_model_inventory` | medium | Each AI model/component in the design has a clear named owner and purpose. |
| `gov_audit_trail` | high | AI decisions/outputs are logged with enough context (input, model version, output) to be reconstructed later. |
| `gov_accountability` | medium | It's clear who is accountable when the AI produces an incorrect or harmful output. |

---

## Provenance

Exported from `rubric/rubric.json` — the file `backend/rubric.py` loads and the
evaluate stage renders into its prompt. That JSON is the source of truth; this
file is a copy of it for people who should not have to read JSON.

`backend/tests/test_labeling_template.py` asserts every check id, description,
pillar and framework here still matches the rubric. So a check added to, removed
from, or reworded in the JSON without updating this file fails the test suite
with the check named — the export cannot silently go stale.
