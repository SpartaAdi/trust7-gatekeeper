# Statement of Work (SYNTHETIC TEST FIXTURE)

## Project: Internal Expense Claim Portal (fictional)

A small internal web portal lets employees submit expense claims and lets
finance approve them. Roughly 400 internal users, all in one country. This
document is invented purely to exercise the review pipeline and describes no
real client engagement.

### Scope

- A React single-page app served as static files through a CDN.
- A Python API behind a public HTTPS endpoint.
- A managed relational database holding claim records.
- Receipt images stored in an object store.

### Stated design decisions

- Authentication is username and password held in the application database.
  There is no SSO, no MFA, and no role model beyond "employee" and "finance".
- Claim records include employee name, employee ID, and bank account number.
- Nothing in this design states an encryption-at-rest setting for the database
  or the object store.
- Traffic from the browser to the load balancer is HTTPS. Traffic from the load
  balancer to the API instance is plain HTTP on the internal network.
- Receipt images are written to a bucket that is readable by the whole account.
- Application credentials are read from a configuration file deployed with the
  application. There is no secrets store.
- Database backups are taken manually before each release. No restore has been
  documented or attempted.
- Logging writes the full request body, including form fields, to a log group.
  There is no alerting, no dashboard, and no on-call runbook.
- No autoscaling is configured; one instance runs continuously, sized for the
  400-user population.
- The database runs in a single availability zone.
- There is no staging environment; changes deploy straight to production, and
  there is no documented rollback path.
- Infrastructure is created by hand in the cloud console. Nothing is defined as
  code.
- Costs are not currently tracked per environment or per team.
- No model, AI, or machine-learning component is used anywhere in this system,
  and none is planned. Every decision in the workflow is made by a person.
- The region the workload runs in is not stated anywhere in this document, and
  no data residency requirement has been recorded.

### Out of scope

- Disaster recovery to a second region. No RTO or RPO has been agreed.
- Any formal data retention or deletion schedule.
- Retries, timeouts, or circuit breakers between the API and its dependencies.
