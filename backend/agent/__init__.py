"""Agent: the multi-stage review pipeline.

classify components -> evaluate against rubric -> prioritize findings
-> generate remediation

Model calls go through llm.py, which selects a provider from LLM_PROVIDER;
pay-per-token only, no provisioned throughput.

"""
