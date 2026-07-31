# Architecture Design Document: "TechAssist" Internal RAG Portal

## 1. Executive Summary
TechAssist is an internal generative AI chatbot deployed to our Tier-2 customer success team. It enables agents to instantly query millions of pages of product documentation to resolve complex customer tickets. The system operates autonomously, providing answers directly to the user interface without a human-in-the-loop review step for the generated text.

## 2. Infrastructure & Compute Topology
The frontend is a React SPA delivered via Amazon CloudFront (S3 origin). Amazon API Gateway handles routing to our backend microservices, which run on AWS ECS Fargate (Python/FastAPI). Fargate auto-scales based on CPU utilization, easily accommodating our spiky intraday call volumes without over-provisioning. 

Our CI/CD pipeline uses GitHub Actions to trigger Terraform, ensuring all AWS infrastructure is defined as code. Deployments use AWS CodeDeploy for automated Blue/Green rollouts with safe rollback thresholds.

## 3. Data Flow & AI Integration
The ingestion pipeline uses AWS Step Functions to trigger Glue jobs that extract and chunk PDFs from S3. We use the Amazon Titan Multimodal model to create embeddings, which are stored in Amazon OpenSearch Serverless (chosen for its native K-NN similarity search performance). 

For inference, the backend uses a custom `FoundationModelGateway` SDK. This abstracts our Bedrock calls, meaning we can easily swap our provider to an open-source Llama model on SageMaker if vendor lock-in becomes an issue. Semantic caching via Amazon ElastiCache (Redis) catches repeated queries, skipping the Bedrock invocation entirely.

We employ an LLM router: simple intent classification goes to Claude 3 Haiku, while complex summarization routes to Claude 3.5 Sonnet. This reduces our cost per query (which is logged granularly in DynamoDB) and lowers our inference energy footprint. The RAG architecture heavily restricts the LLM to only use the retrieved OpenSearch context, controlling hallucinations, and every response includes a strict citation linking back to the source PDF. Jane Doe (VP of Data Science) is the named owner of this model and holds ultimate accountability for its outputs.

## 4. Security & Compliance
End-users access the tool via AWS Cognito (OIDC federated to corporate Active Directory with MFA). They must complete a mandatory Wiki training module on AI limitations before being granted IAM access. 

IAM Roles for Service Accounts (IRSA) restrict container permissions. A custom Python middleware and Amazon Macie scrub PII from user prompts before they reach Bedrock. Data at rest (S3, DynamoDB, OpenSearch) uses KMS Customer Managed Keys. Internal services use App Mesh for mTLS 1.3. AWS WAF and VPC Endpoints (PrivateLink) ensure no database or Bedrock traffic traverses the public internet. CloudTrail is configured with log file validation to ensure tamper-resistant audit logs. Thorough red-teaming and threat modeling for prompt injection and data poisoning were completed last month. 

*Note: DevOps currently injects our Datadog APM and legacy CRM API keys as plaintext environment variables directly into the ECS task definitions. We will move this to Secrets Manager in Q4.*

## 5. Operations & Resiliency
Datadog and AWS X-Ray provide our distributed tracing. Multi-AZ is enabled across ECS, OpenSearch, and NAT Gateways. DynamoDB uses Point-in-Time Recovery (PITR). We are targeting a 2-hour RTO and 15-minute RPO. We have localized this deployment strictly to the `eu-north-1` region to leverage its high renewable energy mix and enforce European data residency.

**Current Operational Risks:**
*   **Storage Costs:** All ingested raw PDFs and session transcripts remain in the `support-raw-logs` S3 bucket indefinitely with no TTL or Glacier transition policy.
*   **Integration Failures:** Synchronous calls to the downstream CRM lack circuit breakers or retry limits. A CRM timeout currently crashes the parent ECS task.
*   **Incident Response:** Standard SOPs and runbooks for general operational outages are still being drafted. Furthermore, there is no specialized response path for AI-specific failures (e.g., severe model drift or prompt jailbreaks). 
*   **Fairness:** No evaluations have been conducted to see if the bot's accuracy varies across different demographic segments of our user base.