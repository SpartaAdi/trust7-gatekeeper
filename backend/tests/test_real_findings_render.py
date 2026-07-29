"""Genuine model output from a live run, through the real rendering path.

The dense-copy round was blocked on this: the instruction was to verify against
real findings rather than a synthetic example. These two strings are verbatim
output from an actual review, and they are here because what they revealed is
not what the round assumed.

WHAT THEY SHOW: the model wrote multi-step remediation as RUN-ON PROSE, with no
newlines and no markers. So the bullet detection added in the dense-copy round
correctly classifies both as prose and renders a paragraph — it does not
mis-split them, which is the failure that would have mattered, but it also does
not improve them. Only the prompt change can do that, and these samples predate
it. They are kept as the regression fixture for the safe half of that guarantee.
"""

from __future__ import annotations

import io

import report
from pypdf import PdfReader
from schema import Finding, FrameworkScore, ReviewResult

# Verbatim. Do not tidy these — the point is that they are not tidy.
REMEDIATION_TLS = (
    "Reconfigure the ALB-to-API target group to use HTTPS on port 443 with a TLS "
    "certificate on the EC2 instance... Enable RDS SSL/TLS by setting..."
)
EVIDENCE_TLS = (
    "Data flow from ALB to API is explicitly listed as 'HTTP (internal)' (not "
    "HTTPS)... the design does not establish TLS/SSL for ALB->API, API->DB..."
)
REMEDIATION_AUTH = (
    "Replace local database username/password authentication with Amazon "
    "Cognito... Enforce MFA for all users. Define Cognito groups or custom claims "
    "for RBAC..."
)
EVIDENCE_AUTH = (
    "Authentication is explicitly 'username and password held in the application "
    "database.' There is no SSO, MFA, centralized identity provider..."
)


def test_real_remediation_is_recognised_as_prose_not_mis_split() -> None:
    """The safe half of the guarantee, and the one that could have gone wrong.

    Both of these are genuinely multi-step. A looser detector — one that split on
    sentence boundaries, or accepted a single marker — would have chopped them
    into fragments mid-sentence.
    """
    for text in (REMEDIATION_TLS, REMEDIATION_AUTH, EVIDENCE_TLS, EVIDENCE_AUTH):
        assert report.structured_lines(text) is None


def test_real_findings_render_into_a_valid_pdf() -> None:
    pdf = report.build_pdf(_review())

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 5000


def test_the_arrow_and_quotes_in_real_evidence_survive_escaping() -> None:
    """`ALB->API` and `'HTTP (internal)'` go through ReportLab's markup parser.

    Angle brackets are exactly what `_t()` exists for: unescaped, `->` is
    harmless but `<b>` would restyle the document, and both take the same path.
    """
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(report.build_pdf(_review()))).pages)

    assert "ALB->API" in text
    assert "HTTP (internal)" in text
    assert "Amazon Cognito" in text


def _review() -> ReviewResult:
    return ReviewResult(
        review_id="11111111-1111-1111-1111-111111111111",
        created_at="2026-07-29T10:00:00Z",
        title="Claims portal",
        overall_score=41.0,
        frameworks=[
            FrameworkScore(framework="aws_waf", framework_name="AWS", score=41.0)
        ],
        summary="Prose assessment.",
        executive_summary="Below band.",
        findings=[
            Finding(
                framework="aws_waf", pillar_id="security",
                check_id="sec_encryption_transit", status="fail", severity="high",
                title="No TLS on internal data flows", evidence=EVIDENCE_TLS,
                remediation=REMEDIATION_TLS, remediation_effort="low", priority=1,
            ),
            Finding(
                framework="aws_waf", pillar_id="security",
                check_id="sec_identity_auth", status="fail", severity="high",
                title="Local username/password authentication",
                evidence=EVIDENCE_AUTH, remediation=REMEDIATION_AUTH,
                remediation_effort="medium", priority=2,
            ),
        ],
    )
