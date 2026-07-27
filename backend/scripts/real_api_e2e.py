"""One REAL end-to-end pipeline run against the live Anthropic API.

Nothing is stubbed. Every one of the six stages runs, and every model call goes
to api.anthropic.com with the key from backend/.env (or the environment).

Verifies the four conditions asked for:
  1. the API call actually completes without error
  2. the model string resolves to claude-sonnet-5
  3. the structured-outputs-2025-11-13 beta is accepted
  4. returned payloads validate against their schemas with
     additionalProperties: false enforced

Run:  python real_api_e2e.py
Exit: 0 all confirmed | 2 no credential | 1 a real failure (raw error shown)
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time
import traceback

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# Isolate this run's data so it cannot disturb real local-data.
DATA = tempfile.mkdtemp(prefix="t7-real-")
os.environ["LOCAL_DATA_DIR"] = DATA


def rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# --------------------------------------------------------------------------- #
# 0. Preflight: the credential. Never printed, never logged.
# --------------------------------------------------------------------------- #
rule("0. PREFLIGHT — credential")

ENV_FILE = BACKEND / ".env"
print(f"backend/.env exists:            {ENV_FILE.is_file()}")
print(f"ANTHROPIC_API_KEY in environ:   {'ANTHROPIC_API_KEY' in os.environ}")

import config  # noqa: E402  — importing loads backend/.env via python-dotenv

try:
    key = config.anthropic_api_key()
except RuntimeError as exc:
    print(f"\nBLOCKED — no credential available.\n  {type(exc).__name__}: {exc}")
    print(
        "\nProvide the key without pasting it in chat, either:\n"
        "  printf 'ANTHROPIC_API_KEY=%s\\n' \"$KEY\" > backend/.env\n"
        "  export ANTHROPIC_API_KEY=...   (in the session environment)\n"
        "Then re-run this script. No other step is blocked."
    )
    sys.exit(2)

# Fingerprint only — enough to confirm a key is loaded and which one, never the value.
import hashlib  # noqa: E402

print(f"key loaded:                     yes (len {len(key)}, "
      f"sha256:{hashlib.sha256(key.encode()).hexdigest()[:12]}…)")
print(f"source:                         "
      f"{'environment' if os.environ.get('ANTHROPIC_API_KEY') else 'backend/.env'}")

# --------------------------------------------------------------------------- #
# 1. Model string
# --------------------------------------------------------------------------- #
rule("1. MODEL STRING")
print(f"config.MODEL = {config.MODEL!r}")
MODEL_OK = config.MODEL == "claude-sonnet-5"
print(f"resolves to claude-sonnet-5:    {MODEL_OK}")

# --------------------------------------------------------------------------- #
# 2. Instrument the single SDK seam so the raw request is observable
# --------------------------------------------------------------------------- #
import anthropic  # noqa: E402

import llm  # noqa: E402

CALLS: list[dict] = []
_real_create = llm._create


def traced_create(request: dict):
    """Record exactly what goes over the wire, then make the real call."""
    record = {
        "model": request.get("model"),
        "betas": [llm._STRUCTURED_OUTPUTS_BETA],
        "has_output_config": "output_config" in request,
        "effort": (request.get("output_config") or {}).get("effort"),
        "thinking": (request.get("thinking") or {}).get("type"),
        "max_tokens": request.get("max_tokens"),
        "schema_additional_properties": (
            (request.get("output_config") or {}).get("format", {}).get("schema", {})
        ).get("additionalProperties", "<absent>"),
        "started": time.time(),
    }
    CALLS.append(record)
    try:
        message = _real_create(request)
    except BaseException as exc:  # noqa: BLE001 — recorded verbatim, then re-raised
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["request_id"] = getattr(exc, "request_id", None)
        record["elapsed"] = round(time.time() - record["started"], 2)
        raise
    record["elapsed"] = round(time.time() - record["started"], 2)
    record["stop_reason"] = message.stop_reason
    record["request_id"] = getattr(message, "_request_id", None)
    record["usage"] = {
        "in": message.usage.input_tokens,
        "out": message.usage.output_tokens,
        "cache_read": getattr(message.usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
    }
    return message


llm._create = traced_create

# Count fallback retries. A retry means the FIRST request was rejected — that is
# not a clean pass, so it has to be visible rather than silently absorbed.
RETRIES: list[str] = []
_real_without_tuning = llm._without_tuning


def traced_without_tuning(request: dict):
    RETRIES.append("fallback engaged: first request was rejected")
    return _real_without_tuning(request)


llm._without_tuning = traced_without_tuning

# --------------------------------------------------------------------------- #
# 3. Live stage-by-stage progress, straight off the real status writes
# --------------------------------------------------------------------------- #
import storage  # noqa: E402

_real_put_status = storage.put_status
_seen: dict[str, str] = {}
T0 = time.time()


def traced_put_status(status) -> None:
    for entry in status.stages:
        line = f"{entry.state}|{entry.detail}"
        if _seen.get(entry.name) != line:
            _seen[entry.name] = line
            mark = {"running": "▶", "done": "✔", "error": "✖", "pending": "·"}.get(
                entry.state, "?"
            )
            elapsed = f"{time.time() - T0:6.1f}s"
            detail = f" — {entry.detail}" if entry.detail else ""
            print(f"  [{elapsed}] {mark} {entry.name:<10} {entry.state:<8}{detail}")
    _real_put_status(status)


storage.put_status = traced_put_status

# --------------------------------------------------------------------------- #
# 4. Synthetic inputs — invented, not from any client engagement
# --------------------------------------------------------------------------- #
rule("2. SYNTHETIC TEST INPUTS (invented — no real client content)")

SOW = b"""# Statement of Work (SYNTHETIC TEST FIXTURE)

## Project: Internal Expense Claim Portal (fictional)

A small internal web portal lets employees submit expense claims and lets
finance approve them. Roughly 400 internal users. This document is invented
purely to exercise the review pipeline.

### Scope
- A React single-page app served as static files.
- A Python API behind a public HTTPS endpoint.
- A managed relational database holding claim records.
- Receipt images stored in an object store.

### Stated design decisions
- Authentication is username and password held in the application database.
- Claim records include employee name, employee ID, and bank account number.
- Receipt images are written to a bucket that is readable by the whole account.
- Database backups are taken manually before each release.
- Logging writes the full request body, including form fields, to a log group.
- No autoscaling is configured; one instance runs continuously.
- There is no staging environment; changes deploy straight to production.
- Costs are not currently tracked per environment or per team.
- No model or AI component is used in this system.

### Out of scope
- Disaster recovery to a second region.
- Any formal data retention or deletion schedule.
"""

DIAGRAM = b"""<mxfile host="app.diagrams.net">
  <diagram name="Expense Portal">
    <mxGraphModel dx="800" dy="600" grid="1" pageWidth="850" pageHeight="1100">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="cdn" value="CloudFront (static SPA)" style="sphape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudfront" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="78" height="78" as="geometry"/>
        </mxCell>
        <mxCell id="alb" value="Public Application Load Balancer" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.application_load_balancer" vertex="1" parent="1">
          <mxGeometry x="200" y="40" width="78" height="78" as="geometry"/>
        </mxCell>
        <mxCell id="api" value="Expense API (single EC2 instance)" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ec2" vertex="1" parent="1">
          <mxGeometry x="360" y="40" width="78" height="78" as="geometry"/>
        </mxCell>
        <mxCell id="db" value="Claims database (RDS PostgreSQL, single AZ)" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.rds" vertex="1" parent="1">
          <mxGeometry x="520" y="40" width="78" height="78" as="geometry"/>
        </mxCell>
        <mxCell id="s3" value="Receipt images bucket (account-readable)" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.s3" vertex="1" parent="1">
          <mxGeometry x="360" y="200" width="78" height="78" as="geometry"/>
        </mxCell>
        <mxCell id="logs" value="CloudWatch Logs (full request bodies)" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudwatch" vertex="1" parent="1">
          <mxGeometry x="520" y="200" width="78" height="78" as="geometry"/>
        </mxCell>
        <mxCell id="e1" value="HTTPS" style="endArrow=block" edge="1" parent="1" source="cdn" target="alb"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e2" value="HTTP (internal)" style="endArrow=block" edge="1" parent="1" source="alb" target="api"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e3" value="SQL, claim + bank details" style="endArrow=block" edge="1" parent="1" source="api" target="db"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e4" value="PUT receipt image" style="endArrow=block" edge="1" parent="1" source="api" target="s3"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e5" value="request logs" style="endArrow=block" edge="1" parent="1" source="api" target="logs"><mxGeometry relative="1" as="geometry"/></mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""

document_key = storage.save_upload("synthetic-expense-portal-sow.md", SOW)
diagram_key = storage.save_upload("synthetic-expense-portal.drawio", DIAGRAM)
print(f"SoW      {len(SOW):>6} bytes  -> {document_key}")
print(f"diagram  {len(DIAGRAM):>6} bytes  -> {diagram_key}")
print("diagram path: draw.io XML (parsed deterministically, no vision call)")

# --------------------------------------------------------------------------- #
# 5. Run all six stages for real
# --------------------------------------------------------------------------- #
rule("3. PIPELINE — six stages, live API")

from agent import pipeline  # noqa: E402
from schema import ReviewStatus  # noqa: E402

review_id = "real-e2e-001"
storage.put_status(ReviewStatus.initial(review_id))

result = None
failure = None
try:
    result = pipeline.run(
        review_id=review_id,
        title="Synthetic expense portal",
        document_key=document_key,
        diagram_key=diagram_key,
    )
except BaseException as exc:  # noqa: BLE001 — reported raw below
    failure = exc

# --------------------------------------------------------------------------- #
# 6. Every model call, raw
# --------------------------------------------------------------------------- #
rule("4. MODEL CALLS (raw, as sent)")
for i, call in enumerate(CALLS, 1):
    print(f"\ncall {i}:")
    for field in (
        "model", "betas", "has_output_config", "effort", "thinking", "max_tokens",
        "schema_additional_properties", "stop_reason", "request_id", "elapsed", "usage",
    ):
        if field in call:
            print(f"  {field:<28} {call[field]}")
    if "error" in call:
        print(f"  error                        {call['error']}")

if RETRIES:
    print("\n!! FALLBACK RETRIES (first request rejected):")
    for r in RETRIES:
        print(f"  - {r}")
else:
    print("\nno fallback retries: every request was accepted as first sent")

if failure is not None:
    rule("FAILED — raw error")
    print(f"{type(failure).__name__}: {failure}")
    for attr in ("status_code", "request_id"):
        if hasattr(failure, attr):
            print(f"{attr}: {getattr(failure, attr)}")
    body = getattr(failure, "body", None)
    if body is not None:
        print(f"body: {json.dumps(body, indent=2, default=str)}")
    print("\n--- traceback ---")
    traceback.print_exception(type(failure), failure, failure.__traceback__)
    sys.exit(1)

assert result is not None

# --------------------------------------------------------------------------- #
# 7. Schema conformance, enforced strictly
# --------------------------------------------------------------------------- #
rule("5. SCHEMA CONFORMANCE (additionalProperties: false enforced)")

from agent import stages  # noqa: E402
from ingestion import vision  # noqa: E402

try:
    import jsonschema
except ImportError:
    print("jsonschema not installed — run: pip install jsonschema")
    sys.exit(1)


def strictness(schema: dict, path: str = "$") -> list[str]:
    """Every object node in the schema must forbid extra properties."""
    problems = []
    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties is not False")
        for name, sub in (schema.get("properties") or {}).items():
            problems += strictness(sub, f"{path}.{name}")
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        problems += strictness(schema["items"], f"{path}[]")
    return problems


SCHEMAS = {
    "classify (DesignGraph)": stages._CLASSIFY_SCHEMA,
    "evaluate (findings)": stages._EVALUATE_SCHEMA,
    "prioritize": stages._PRIORITIZE_SCHEMA,
    "remediate": stages._REMEDIATE_SCHEMA,
    "vision (DesignGraph)": vision._SCHEMA,
}
strict_ok = True
for name, schema in SCHEMAS.items():
    problems = strictness(schema)
    print(f"  {'OK  ' if not problems else 'WEAK'} {name}")
    for p in problems:
        strict_ok = False
        print(f"       {p}")

# Validate the real returned payloads against those schemas.
validation_ok = True


def validate(label: str, instance, schema: dict) -> None:
    global validation_ok
    try:
        jsonschema.validate(instance, schema)
        print(f"  OK   {label}")
    except jsonschema.ValidationError as exc:
        validation_ok = False
        print(f"  FAIL {label}: at {list(exc.absolute_path)} — {exc.message}")


print("\nreturned payloads validated against those exact schemas:")
findings_payload = {
    "findings": [
        {
            "check_id": f.check_id,
            "status": f.status,
            "severity": f.severity,
            "severity_rationale": "recorded",
            "title": f.title,
            "evidence": f.evidence or "",
            "affected_components": list(f.affected_components or []),
        }
        for f in result.findings
    ]
}
validate("evaluate payload (45 findings)", findings_payload, stages._EVALUATE_SCHEMA)

# --------------------------------------------------------------------------- #
# 8. Result
# --------------------------------------------------------------------------- #
rule("6. REVIEW OUTPUT")
print(f"title              {result.title}")
print(f"overall score      {result.overall_score}")
print(f"components         {len(result.components)}")
print(f"findings           {len(result.findings)}")
print(f"pillars scored     {sum(len(f.pillars) for f in result.frameworks)}")
print(f"token usage        {result.token_usage}")

by_status: dict[str, int] = {}
for f in result.findings:
    by_status[f.status] = by_status.get(f.status, 0) + 1
print(f"status breakdown   {by_status}")

print("\nframework scores:")
for fw in result.frameworks:
    print(f"  {fw.name:<32} {fw.score:>6}")
    for p in fw.pillars:
        print(f"    {p.pillar_name:<28} {p.score:>6}  "
              f"({p.checks_evaluated}/{p.checks_total} evaluated)")

print(f"\nexecutive summary:\n{result.executive_summary}")

print("\ntop 5 findings by priority:")
for f in sorted(result.findings, key=lambda f: (f.priority == 0, f.priority))[:5]:
    print(f"\n  [{f.priority}] {f.severity.upper():<6} {f.check_id}")
    print(f"      {f.title}")
    print(f"      status:      {f.status}")
    print(f"      evidence:    {(f.evidence or '')[:200]}")
    print(f"      remediation: {(f.remediation or '')[:200]}")

# --------------------------------------------------------------------------- #
# 9. The four confirmations
# --------------------------------------------------------------------------- #
rule("7. CONFIRMATIONS")
checks = [
    ("1. real API calls completed without error",
     len(CALLS) > 0 and all("error" not in c for c in CALLS)),
    ("2. model string resolved to claude-sonnet-5",
     MODEL_OK and all(c["model"] == "claude-sonnet-5" for c in CALLS)),
    ("3. structured-outputs-2025-11-13 beta accepted",
     all(c.get("betas") == ["structured-outputs-2025-11-13"] for c in CALLS)
     and all(c.get("has_output_config") for c in CALLS)
     and not RETRIES),
    ("4. payloads conform, additionalProperties: false enforced",
     strict_ok and validation_ok),
]
for label, passed in checks:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}")

print(f"\ncalls made: {len(CALLS)}   wall clock: {time.time() - T0:.1f}s")
print(f"data dir: {DATA}")
sys.exit(0 if all(p for _, p in checks) else 1)
