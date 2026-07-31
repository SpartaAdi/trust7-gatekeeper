# Statement of Work: Global Checkout & Payments API

## 1. Platform Overview
This document outlines the architecture for the tier-1 REST API backend processing secure customer checkouts, payments, and order state management for the global online retail platform. 

## 2. Compute & Network Topology
Traffic routes through Amazon Route 53 to an Application Load Balancer (ALB). The core business logic runs on Java Spring Boot microservices inside Amazon EKS. Event sourcing and asynchronous order state changes are managed by Amazon MSK (Managed Streaming for Apache Kafka). 

The network perimeter is guarded by AWS WAF and Shield Advanced. EKS nodes and databases sit in private subnets with strict Security Groups. EKS Horizontal Pod Autoscaler (HPA) manages burst traffic elastically. HashiCorp Terraform acts as the definitive Infrastructure as Code (IaC) standard. 

*Release Engineering Note: While infrastructure is automated, application container deployments to EKS are currently executed manually via `kubectl apply` commands from a centralized bastion jump-box by the lead developer.*

## 3. Dynamic Risk & Fraud Engine
To protect against payment fraud, the checkout flow incorporates a smart, dynamic risk-scoring engine. As transactions arrive, a Java-based decision tree evaluates the payload in real-time. It applies dynamic risk scores based on hardcoded geographic distance thresholds, transaction velocity metrics, and IP reputation lists. This logic is heavily reliant on complex stored procedures within the database and regex pattern matching. It does not utilize any foundation models, neural networks, or generative capabilities.

## 4. Data Tier & Storage
The primary datastore is Amazon Aurora PostgreSQL Global Database, perfectly aligning with the ACID compliance required for financial transactions. We use 3-year Reserved Instances (RIs) to cover the predictable baseline database load. Amazon ElastiCache for Redis stores ephemeral cart session data to prevent expensive and repeated database reads. CloudFront caches static product assets globally.

Amazon S3 Lifecycle rules are rigorously configured to transition aged order receipt blobs to S3 Glacier Deep Archive, and ultimately delete them after 7 years to minimize data footprint. 

*Batch Processing Debt: While the main API is containerized, our asynchronous nightly financial settlement reconciliation jobs still run on fixed, oversized legacy Amazon EC2 `m5.4xlarge` instances that run 24/7.*

## 5. Resiliency & Observability
AWS CloudWatch and Prometheus/Grafana handle all metric collection and alerting. Any P1/P2 system failures trigger automated PagerDuty runbooks. EKS nodes and Aurora instances span 3 Availability Zones. Aurora read replicas in our secondary region guarantee an RPO of 5 minutes and an RTO of 15 minutes. Automated continuous backups and nightly RDS snapshots are enabled and tested monthly. We utilize Resilience4j to implement strict circuit breakers and timeouts, ensuring third-party payment gateway outages do not cascade into our core systems.

## 6. Security Posture
Identity management utilizes OAuth2 via Auth0 for customer and system-level API access. IAM roles enforce strict least privilege. AWS Secrets Manager securely stores and automatically rotates all database and payment gateway credentials. Data in transit is secured via TLS 1.2 externally and mTLS internally between pods. Aurora databases are encrypted at rest with AWS KMS. 

Cost Explorer tagging (`Project: GlobalCheckout`) is strictly mandated on all AWS resources.

*Audit Gap: Standard CloudWatch logs are used for troubleshooting, but dedicated, tamper-resistant security audit logging (e.g., CloudTrail log file validation or centralized SIEM forwarding) is not currently implemented.*