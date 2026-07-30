# Statement of Work (SYNTHETIC TEST FIXTURE)

## Project: Policy Document Triage Assistant (fictional)

An internal assistant reads inbound insurance policy documents, extracts key
fields, classifies each document into one of six handling queues, and drafts a
summary for a human handler. Around 120 handlers, around 8,000 documents a day.
This document is invented purely to exercise the review pipeline and describes
no real client engagement.

### Scope

- Documents arrive in an object store bucket and are placed on a managed queue.
- A container service reads the queue, calls a hosted foundation model for
  extraction and classification, and writes results to a document database.
- Handlers review every draft summary in a web console before it is actioned.
- A retrieval index over the current policy handbook grounds the summaries.

### Stated design decisions

- All infrastructure is defined in Terraform. Console changes are not permitted
  and are blocked by policy.
- The container service runs across three availability zones behind a managed
  load balancer, autoscaling from 2 to 20 tasks on queue depth.
- The container service and the document database sit in private subnets. Only
  the load balancer is reachable from outside the VPC.
- Handler access to the console is through the corporate SSO provider with MFA
  enforced. Two roles exist: handler and supervisor.
- The queue moves a message to a dead-letter queue after three delivery
  attempts. Model calls use a 30-second timeout and two retries with backoff.
- Every model call is written to an append-only audit log: the prompt, the model
  version, the retrieved passages, the returned output, and the handler's
  eventual decision against their user id. The log is retained for seven years
  to meet the regulator's record-keeping requirement.
- Model outputs are never actioned automatically. A handler accepts, edits, or
  rejects each draft, and the console shows the retrieved passages beside the
  draft so the handler can see what the summary was built from.
- Extraction output is validated against a JSON schema. Any field the model
  cannot ground in a retrieved passage is returned empty rather than guessed.
- Policyholder name, address, and policy number appear in every document. They
  are sent to the model as they appear in the document; no masking, redaction or
  minimization step exists before the model call.
- Documents are encrypted at rest with a customer-managed key. The queue and the
  document database are encrypted at rest. TLS 1.2 or higher is required on
  every hop, including the call to the model provider.
- The container service authenticates to the model provider with a short-lived
  task role scoped to a single model id. No long-lived keys exist anywhere in
  the system, and the provider credential is held in a managed secrets store.
- The model provider is called through an internal `LlmClient` interface with one
  implementation per provider, so a second provider can be added without
  changing any call site. A second provider has been identified as an exit path.
- All processing runs in the ap-south-1 region and uses that region's model
  endpoint. No document may leave India; this is a contractual requirement and
  is enforced by the region pinning above.
- The per-document model cost is recorded alongside each audit log entry and
  rolled up daily per queue.
- A smaller, cheaper model performs the six-way queue classification. The larger
  model is used only for the drafted summary.
- Dashboards and alerts cover queue depth, model error rate, model latency, and
  the handler rejection rate. An on-call runbook covers queue backlog and
  provider outage.
- Backups of the document database are automated daily.

### Known gaps, stated for the reviewer

- Cost tagging exists per environment but not per team.
- There is no disaster recovery plan and no RTO or RPO has been agreed.
- Identical documents are re-processed from scratch. No response caching of any
  kind exists.
- No prompt-injection review has been carried out on document text, even though
  that text is supplied by outside parties.
- Summary drafts are not evaluated for variation in quality across document
  languages or across regions.
- No red-teaming or pre-production evaluation of the model has been run. The
  model was selected on the vendor's published benchmarks.
- Handlers received a one-hour demonstration. No written guidance on
  interpreting or overriding a draft exists.
- No database restore has ever been tested.
- No deployment rollback path is documented, and there is no staged or
  progressive rollout.
- No owner is named for either model, and no individual or team is recorded as
  accountable for an incorrect model output.
- No lifecycle or expiry policy exists on the document bucket; documents are
  kept indefinitely.
