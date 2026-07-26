"""Agent: the multi-stage review pipeline.

classify components -> evaluate against rubric -> prioritize findings
-> generate remediation

Calls the Anthropic API directly (not Bedrock), pay-per-token only.

Not yet implemented.
"""
