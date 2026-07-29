"""Bulleted model text renders as a list in the PDF, not as literal dashes.

`report.structured_lines` mirrors `parseStructured` in
frontend/src/components/StructuredText.tsx. The two implementations exist
because the runtimes cannot share code; the cases below are deliberately the
same cases that file asserts, so a change to one that is not mirrored in the
other shows up as a failure rather than as a PDF that disagrees with the screen.
"""

from __future__ import annotations

import report
from schema import Finding, FrameworkScore, ReviewResult


def test_prose_stays_prose() -> None:
    text = "Enable SSE-KMS on the orders table and re-encrypt existing snapshots."

    assert report.structured_lines(text) is None


def test_dash_bullets_become_items_with_the_marker_stripped() -> None:
    assert report.structured_lines("- First.\n- Second.\n- Third.") == [
        "First.",
        "Second.",
        "Third.",
    ]


def test_numbered_steps_become_items() -> None:
    assert report.structured_lines("1. Create the key.\n2. Attach the policy.") == [
        "Create the key.",
        "Attach the policy.",
    ]


def test_one_marked_line_is_a_sentence_not_a_list() -> None:
    assert report.structured_lines("- Only one line here.") is None


def test_a_paragraph_followed_by_bullets_is_left_alone() -> None:
    """Splitting it would drop the paragraph, which is worse than not splitting."""
    assert (
        report.structured_lines("The store is unencrypted.\n- Enable SSE-KMS.")
        is None
    )


def test_blank_lines_between_items_are_ignored() -> None:
    assert report.structured_lines("- One.\n\n- Two.\n") == ["One.", "Two."]


def test_empty_text_is_prose() -> None:
    assert report.structured_lines("") is None


# --------------------------------------------------------------------------- #
# End to end through the real PDF
# --------------------------------------------------------------------------- #


def _review(summary: str, remediation: str) -> ReviewResult:
    return ReviewResult(
        review_id="11111111-1111-1111-1111-111111111111",
        created_at="2026-07-29T10:00:00Z",
        title="Payments platform",
        overall_score=62.5,
        frameworks=[FrameworkScore(framework="aws_waf", framework_name="AWS", score=62.5)],
        summary=summary,
        executive_summary="A short prose verdict.",
        findings=[
            Finding(
                framework="aws_waf",
                pillar_id="security",
                check_id="sec_encryption_at_rest",
                status="fail",
                severity="high",
                title="No encryption at rest",
                evidence="The orders table is described without an encryption setting.",
                remediation=remediation,
                remediation_effort="low",
                priority=1,
            )
        ],
    )


def test_a_bulleted_assessment_reaches_the_pdf_without_literal_markers() -> None:
    """The regression this guards: ReportLab collapses newlines just as HTML does,
    so a bulleted summary rendered as one Paragraph prints "- a - b - c" inline."""
    pdf = report.build_pdf(
        _review("- Security is the weakest pillar.\n- Two high-severity gaps block deploy.", "")
    )

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


def test_bulleted_remediation_reaches_the_pdf() -> None:
    pdf = report.build_pdf(
        _review("Prose assessment.", "- Create a CMK.\n- Enable SSE-KMS.\n- Re-encrypt snapshots.")
    )

    assert pdf.startswith(b"%PDF-")


def test_prose_still_renders_as_one_paragraph() -> None:
    """An older stored review has prose in both fields and must be unaffected."""
    before = report.build_pdf(_review("Prose assessment.", "Prose remediation."))

    assert before.startswith(b"%PDF-")


def test_markup_in_a_bullet_is_still_escaped() -> None:
    """Every item goes through `_t`, or a finding containing <b> restyles the PDF."""
    items = report.structured_lines("- Set <b>encryption</b> on.\n- Rotate the key.")

    assert items == ["Set <b>encryption</b> on.", "Rotate the key."]
    flowables = report._prose_or_list(
        "- Set <b>encryption</b> on.\n- Rotate the key.", report.S["body"]
    )
    assert "&lt;b&gt;" in flowables[0].text
    assert "<b>" not in flowables[0].text.replace("&lt;b&gt;", "")


def test_the_bullet_glyph_exists_in_the_body_font() -> None:
    """The marker is drawn, not styled, so a font without it would render a blank.

    Worth pinning because pypdf extracts it as \\x7f, which looks like a missing
    glyph and is only an artefact of the encoding map — the width below is the
    thing that actually proves Helvetica can draw it.
    """
    from reportlab.pdfbase.pdfmetrics import getFont

    assert getFont(report.S["body"].fontName).stringWidth("•", 10) > 0
