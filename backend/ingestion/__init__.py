"""Ingestion: document + diagram intake and normalization.

Two diagram paths converge on one common schema:
  - draw.io XML  -> parsed deterministically, no LLM call
  - image upload -> parsed by the configured vision model

"""
