# Statement of Work (SYNTHETIC VERIFICATION FIXTURE)

## Project: Order Intake Platform (fictional)

Invented for verifying a deployment. This describes no real client engagement and
contains no client, personal or commercial information.

It is deliberately MIXED — some controls are genuinely present and some are
genuinely absent — so a review of it produces a spread of pass, partial and fail
rather than a wall of one verdict. A uniform result would not tell you the
pipeline was working, only that it was answering.

## Scope

A public order-intake API for around 900 external customers, with a static web
client and asynchronous downstream processing.

## Stated design decisions

- The web client is static assets served through a CDN.
- The API runs as containers across three availability zones behind a managed
  load balancer. HTTPS at the edge; TLS on every internal hop.
- Customer sign-in is the corporate SSO provider with MFA enforced. Two roles
  exist: customer and operations.
- Orders are held in a managed key-value store, encrypted at rest.
- Receipt documents go to an object store encrypted with a customer-managed key.
  The bucket is not public and is not readable account-wide.
- Provider credentials are held in a managed secrets store. No long-lived keys
  exist in configuration or images.
- Order events go onto a queue with a dead-letter queue after three attempts.
  Downstream calls use a 30-second timeout and two retries with backoff.
- All infrastructure is defined as code; console changes are blocked by policy.
- Dashboards and alerts cover queue depth, API error rate and API latency. An
  on-call runbook covers queue backlog and provider outage.
- Security-relevant actions are written to an append-only audit log.
- Everything runs in ap-south-1. Order data must not leave India; this is
  contractual and enforced by the region pinning.
- No model, AI or machine-learning component is used anywhere in this system.

## Known gaps, stated for the reviewer

- No disaster recovery plan. No RTO or RPO has been agreed, and there is no
  cross-region replication.
- Backups of the order store run daily, but a restore has never been tested.
- No deployment rollback path is documented and there is no staged rollout.
- Cost is tracked per environment but not per team, and no budget is set.
- Nothing caches repeated reads; every request reaches the data store.
- No lifecycle or expiry policy exists on the receipts bucket; documents are kept
  indefinitely.
