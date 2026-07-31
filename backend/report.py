"""PDF export of a finished review.

ReportLab rather than WeasyPrint, on three grounds measured against the current
requirements.txt:

* **Dependency weight.** ReportLab declares two runtime dependencies (pillow,
  charset-normalizer). WeasyPrint declares eight, including cffi, which binds to
  libpango/libgobject at render time. Those shared libraries are present in this
  container but are not guaranteed on Render's native Python runtime, where
  `apt-get` is not available during a free-tier build. ReportLab's failure mode
  is a pip resolution error at build time; WeasyPrint's would be an ImportError
  the first time a user clicks Download, in production.
* **No templating engine is installed.** WeasyPrint consumes HTML/CSS, so it
  would need jinja2 as well — two additions, not one — or hand-concatenated HTML.
* **Escaping.** Every string in this report is model-generated from
  attacker-controlled uploads. ReportLab's `Paragraph` parses a small markup
  dialect, so all of it is escaped through `_t()` below; an HTML pipeline would
  need the same discipline over a much larger surface.

The visual language follows the Minfy palette and the existing history heatmap:
pillar strength is carried by a monochrome navy ramp — opacity, not hue — so the
scorecard stays legible to colour-blind readers and never resorts to a gradient.
"""

from __future__ import annotations

import io
import pathlib
import re
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import pagesizes
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

import maturity
import roadmap
from schema import Finding, ReviewResult

# --------------------------------------------------------------------------- #
# Brand palette — Minfy. No purple, no gradient, no third accent hue.
#
# Every value is the twin of a `@theme` token in `frontend/src/index.css`, named in
# the comment beside it so the pairing is greppable, and asserted equal by
# tests/test_report.py. The report and the dashboard have to agree: a reviewer
# downloads the PDF from the results page, and two accent colours for one review
# reads as two products.
#
# ACCESSIBILITY FIRST, brand identity second — that is the order these were solved
# in. The three brand anchors (NAVY, the ACCENT blue family, LOGO_YELLOW) are the
# sampled minfytech.com values and are fixed. Everything else was derived to clear
# WCAG AA on the WORST surface it is used against, not the average, and
# `scripts/contrast_audit.py` re-derives that claim from this file on demand.
# --------------------------------------------------------------------------- #
NAVY = HexColor("#1B263B")       # --color-minfy-navy
ACCENT = HexColor("#1420BE")     # --color-minfy-indigo — the primary accent
ACCENT_LIGHT = HexColor("#1C55BB")  # --color-minfy-blue
# The accent again, for text sitting ON the navy cover. #1420BE is a dark blue on a
# dark navy — fine on the white body pages, unreadable on the cover. The first fix
# here was #4A81F2, picked by eye; it measured 4.12:1, which passes for the 13pt
# band and FAILS for the 8.5pt eyebrow. This value is solved, not judged: 5.23:1 on
# NAVY, clearing AA body text with margin.
ACCENT_ON_DARK = HexColor("#6896F4")
INK = HexColor("#1B263B")        # --color-ink
WHITE = HexColor("#FFFFFF")
OFF_WHITE = HexColor("#F6F7FD")  # --color-surface-sunken
HAIRLINE = HexColor("#CCD2DC")   # --color-hairline
INK_MUTED = HexColor("#47525E")  # --color-ink-muted

# Flat pastel blocks, as the dashboard uses behind its framework cards. They carry
# no severity meaning; pillar strength is the opacity ramp below, never hue.
#
# Lightened from the raw site samples until INK_MUTED cleared AA on every one of
# them. The blocks read as a subtler tint than the sampled originals; that is the
# deliberate trade, because the sampled sky failed at 4.09:1 under the muted text
# the scorecard actually puts on it.
PASTEL_SKY = HexColor("#EEF5FE")   # --color-pastel-sky — AWS Well-Architected
PASTEL_MINT = HexColor("#E5FAED")  # --color-pastel-mint — Minfy TRUST-7
PASTEL_CREAM = HexColor("#FBF8DE")  # --color-pastel-cream — executive summary

# Cover-only greys, and the logo's yellow. Named rather than inline so
# scripts/contrast_audit.py can read every colour this module paints straight out
# of the source — an inline literal is a colour the audit cannot see.
COVER_META = HexColor("#AEBAC7")
COVER_FOOT = HexColor("#8E9DAC")
LOGO_YELLOW = HexColor("#FDC921")   # --color-minfy-yellow

PAGE = pagesizes.A4
PAGE_W, PAGE_H = PAGE
MARGIN = 18 * mm

SEVERITY_ORDER: tuple[str, ...] = ("high", "medium", "low")
SEVERITY_HEADING = {
    "high": "High severity",
    "medium": "Medium severity",
    "low": "Low severity",
}
STATUS_LABEL = {
    "fail": "Not met",
    "partial": "Partial",
    "pass": "Met",
    "not_applicable": "N/A",
}
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


def _t(value: object) -> str:
    """Escape model-generated text before it reaches ReportLab's markup parser.

    Findings originate in uploaded documents, so a title containing `<b>` or a
    stray `&` must render as those characters rather than restyle the report or
    raise a parse error mid-export.
    """
    return escape(str(value if value is not None else ""))


# At least two marked lines: one sentence that happens to start with a dash is
# prose, not a list.
_MIN_LIST_ITEMS = 2
_BULLET_MARKER = re.compile(r"^\s*[-•*]\s+")
_ORDINAL_MARKER = re.compile(r"^\s*\d+[.)]\s+")


def structured_lines(text: str) -> list[str] | None:
    """Split model text into list items, or None when it is prose.

    Mirrors `parseStructured` in frontend/src/components/StructuredText.tsx. The
    prompts now ask for bullets where scanning beats prose, and without this the
    PDF would print the markers literally inside one run-on paragraph — ReportLab
    collapses newlines exactly as HTML does.

    Every non-empty line must carry the marker. A paragraph followed by two
    bullets is prose containing a list, and splitting it would drop the paragraph.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) < _MIN_LIST_ITEMS:
        return None
    for marker in (_BULLET_MARKER, _ORDINAL_MARKER):
        if all(marker.match(line) for line in lines):
            return [marker.sub("", line) for line in lines]
    return None


def _prose_or_list(text: str, style: Any, bullet: str = "•") -> list[Any]:
    """One Paragraph, or one per item with a hanging bullet.

    A ListFlowable would be the obvious reach, but it does not inherit the
    document's own body style cleanly and the indents it computes fight the
    table cells remediation sits inside. An indented Paragraph per item is
    fewer moving parts and renders identically.
    """
    items = structured_lines(text)
    if items is None:
        return [Paragraph(_t(text), style)]
    return [
        Paragraph(f"{bullet}&nbsp;&nbsp;{_t(item)}", style, bulletText=None)
        for item in items
    ]


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #

def _style(
    name: str,
    size: float,
    leading: float,
    colour: Color = INK,
    bold: bool = False,
    space_before: float = 0,
    space_after: float = 0,
    tracking: float = 0,
) -> ParagraphStyle:
    return ParagraphStyle(
        name=name,
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=leading,
        textColor=colour,
        spaceBefore=space_before,
        spaceAfter=space_after,
        alignment=TA_LEFT,
        charSpace=tracking,
    )


S = {
    "cover_eyebrow": _style("ce", 8.5, 12, ACCENT_ON_DARK, bold=True, tracking=1.6),
    "cover_title": _style("ct", 30, 35, WHITE, bold=True),
    "cover_meta": _style("cm", 10, 15, COVER_META),
    "cover_score": _style("cs", 66, 66, WHITE, bold=True),
    "cover_band": _style("cb", 13, 17, ACCENT_ON_DARK, bold=True),
    "cover_foot": _style("cf", 8, 11, COVER_FOOT),
    "h1": _style("h1", 17, 21, NAVY, bold=True, space_after=2),
    "h2": _style("h2", 12, 16, NAVY, bold=True, space_before=10, space_after=4),
    "h3": _style("h3", 10.5, 14, INK, bold=True),
    "eyebrow": _style("eb", 8, 11, INK_MUTED, bold=True, tracking=1.2),
    "body": _style("bd", 9.5, 14, INK),
    "muted": _style("mu", 9, 13, INK_MUTED),
    "small": _style("sm", 8, 11, INK_MUTED),
    "mono": ParagraphStyle(
        name="mono", fontName="Courier", fontSize=7.8, leading=10, textColor=INK_MUTED
    ),
    "cell": _style("cl", 9, 12, INK),
    "cell_b": _style("cb2", 9, 12, INK, bold=True),
}


# --------------------------------------------------------------------------- #
# Opacity-encoded swatch — the same encoding as the history heatmap
# --------------------------------------------------------------------------- #

def swatch_alpha(score: float, evaluated: bool) -> float:
    """Mirror of the heatmap in frontend/src/views/HistoryView.tsx.

    The 0.12 floor keeps a zero score visible as a cell rather than reading as
    blank; an unevaluated pillar sits below that floor at 0.06.
    """
    if not evaluated:
        return 0.06
    return 0.12 + (min(100.0, max(0.0, score)) / 100.0) * 0.88


def _blend_on_white(colour: Color, alpha: float) -> Color:
    """Flatten alpha into a solid RGB so the swatch needs no transparency group."""
    return Color(
        1 - (1 - colour.red) * alpha,
        1 - (1 - colour.green) * alpha,
        1 - (1 - colour.blue) * alpha,
    )


class Swatch(Flowable):
    """A single opacity-encoded cell. Monochrome by design: no hue coding."""

    def __init__(self, score: float, evaluated: bool, size: float = 7.2 * mm) -> None:
        super().__init__()
        self.width = self.height = size
        self._fill = _blend_on_white(NAVY, swatch_alpha(score, evaluated))
        self._evaluated = evaluated

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(self._fill)
        c.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        if not self._evaluated:
            # A dashed outline distinguishes "not evaluated" from "scored zero"
            # without relying on the fill being read correctly.
            c.setStrokeColor(HAIRLINE)
            c.setDash(1, 1)
            c.rect(0, 0, self.width, self.height, stroke=1, fill=0)


class HRule(Flowable):
    def __init__(self, width: float, colour: Color = HAIRLINE, thickness: float = 0.6):
        super().__init__()
        self.width, self.height, self._c, self._t = width, thickness, colour, thickness

    def draw(self) -> None:
        self.canv.setStrokeColor(self._c)
        self.canv.setLineWidth(self._t)
        self.canv.line(0, 0, self.width, 0)


# --------------------------------------------------------------------------- #
# Page furniture
# --------------------------------------------------------------------------- #

def _draw_minfy_mark(canvas, x: float, y: float, size: float, block: Color) -> None:
    """The Minfy mark: a block with the top-right corner notched out for a square.

    Same geometry as `frontend/src/components/MinfyMark.tsx`, in three rectangles
    rather than one path because that is all the canvas API needs here. `block` is
    white on the navy cover and the brand blue anywhere light. The wordmark is
    deliberately not reproduced — see that component's note.
    """
    canvas.saveState()
    canvas.setFillColor(block)
    canvas.rect(x, y, 0.46 * size, size, stroke=0, fill=1)          # left column
    canvas.rect(x, y, size, 0.50 * size, stroke=0, fill=1)          # bottom half
    canvas.setFillColor(LOGO_YELLOW)
    canvas.rect(x + 0.54 * size, y + 0.58 * size, 0.46 * size, 0.42 * size,
                stroke=0, fill=1)
    canvas.restoreState()


def _cover_background(canvas, _doc) -> None:
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    # One flat accent band, the same indigo the dashboard uses for primary
    # actions — a solid fill, not a gradient.
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_H - 6 * mm, PAGE_W, 6 * mm, stroke=0, fill=1)
    canvas.setFillColor(ACCENT_LIGHT)
    canvas.rect(MARGIN, 34 * mm, 52 * mm, 0.9 * mm, stroke=0, fill=1)
    # Between the accent band and the cover frame, which starts 22mm down.
    _draw_minfy_mark(canvas, MARGIN, PAGE_H - 19 * mm, 8 * mm, WHITE)
    canvas.restoreState()


def _body_furniture(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - 3 * mm, PAGE_W, 3 * mm, stroke=0, fill=1)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(INK_MUTED)
    canvas.drawString(MARGIN, 11 * mm, "Trust7 Gatekeeper · governance review")
    canvas.drawRightString(PAGE_W - MARGIN, 11 * mm, f"Page {doc.page - 1}")
    canvas.setStrokeColor(HAIRLINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 15 * mm, PAGE_W - MARGIN, 15 * mm)
    canvas.restoreState()


def _document(buffer: io.BytesIO, title: str) -> BaseDocTemplate:
    doc = BaseDocTemplate(
        buffer,
        pagesize=PAGE,
        title=f"Trust7 Gatekeeper review — {title}" if title else "Trust7 Gatekeeper review",
        author="Trust7 Gatekeeper",
        subject="AWS Well-Architected and Minfy TRUST-7 design review",
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )
    body_frame = Frame(
        MARGIN, 20 * mm, PAGE_W - 2 * MARGIN, PAGE_H - 20 * mm - 12 * mm, id="body"
    )
    doc.addPageTemplates(
        [
            PageTemplate(
                id="cover",
                frames=[
                    Frame(
                        MARGIN,
                        40 * mm,
                        PAGE_W - 2 * MARGIN,
                        PAGE_H - 40 * mm - 22 * mm,
                        id="cover",
                    )
                ],
                onPage=_cover_background,
            ),
            PageTemplate(id="body", frames=[body_frame], onPage=_body_furniture),
        ]
    )
    return doc


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def _cover(result: ReviewResult) -> list:
    open_findings = [f for f in result.findings if f.status in ("fail", "partial")]
    high = [f for f in open_findings if f.severity == "high"]
    band = maturity.maturity_for(result.overall_score)

    story: list = [
        Spacer(1, 6 * mm),
        Paragraph("MINFY TECHNOLOGIES", S["cover_eyebrow"]),
        Spacer(1, 3 * mm),
        Paragraph("Trust7 Gatekeeper", S["cover_title"]),
        Spacer(1, 2 * mm),
        Paragraph("Solution design governance review", S["cover_meta"]),
        Spacer(1, 14 * mm),
        Paragraph(_t(result.title or "Untitled design"), S["cover_title"]),
        Spacer(1, 4 * mm),
        Paragraph(
            f"Reviewed {_t(_format_date(result.created_at))}", S["cover_meta"]
        ),
        # The large gaps below are load-bearing: they push the score and the stat
        # row into the lower half so the cover reads as a full page rather than a
        # block of text stranded at the top.
        Spacer(1, 46 * mm),
        Paragraph(f"{result.overall_score:.1f}", S["cover_score"]),
        # Leading equals the 66pt size, so without this the band label sits in the
        # score's descender space and the two read as one collided line.
        Spacer(1, 2.5 * mm),
        Paragraph(f"{_t(band)} · overall maturity", S["cover_band"]),
        Spacer(1, 26 * mm),
    ]

    stats = [
        ("Checks evaluated", str(len(result.findings))),
        ("Open findings", str(len(open_findings))),
        ("High severity", str(len(high))),
        ("Frameworks", str(len(result.frameworks))),
    ]
    table = Table(
        [
            [Paragraph(label.upper(), S["cover_eyebrow"]) for label, _ in stats],
            [
                Paragraph(f'<font size="17">{value}</font>', S["cover_title"])
                for _, value in stats
            ],
        ],
        colWidths=[(PAGE_W - 2 * MARGIN) / len(stats)] * len(stats),
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 1), (-1, 1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 10 * mm))
    story.append(
        Paragraph(
            f"Review ID {_t(result.review_id)} · AWS Well-Architected Framework and "
            f"Minfy TRUST-7 · Confidential",
            S["cover_foot"],
        )
    )
    return story


# Which pastel sits behind which framework, matching FRAMEWORK_BLOCK in
# frontend/src/views/ResultsView.tsx. An unrecognised framework still gets a block
# rather than none, so a third framework would read as a card too.
FRAMEWORK_BLOCK = {
    "aws_waf": PASTEL_SKY,
    "trust7": PASTEL_MINT,
}


def _pastel_block(content: Flowable, colour: Color, width: float) -> Table:
    """One flat pastel band behind a flowable, as the dashboard does for cards.

    Deliberately NOT applied behind the scorecard table itself: the pillar swatches
    are alpha flattened onto white by `_blend_on_white`, so on a tinted background
    a weak score would render as a pale *white* cell rather than a pale tint of the
    ramp — the encoding would invert at the low end.
    """
    block = Table([[content]], colWidths=[width])
    block.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colour),
                ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]
        )
    )
    return block


def _scorecard(result: ReviewResult) -> list:
    width = PAGE_W - 2 * MARGIN
    story: list = [
        Paragraph("Pillar scorecard", S["h1"]),
        Paragraph(
            "All 13 pillars across both frameworks. Cell shading encodes strength "
            "as depth of a single navy tone — not colour — so it stays readable in "
            "greyscale and to colour-blind readers. A dashed cell was not evaluated.",
            S["muted"],
        ),
        Spacer(1, 5 * mm),
    ]

    for framework in result.frameworks:
        story.append(
            _pastel_block(
                Paragraph(
                    f"{_t(framework.framework_name)} "
                    f'<font color="#47525E" size="9">— {framework.score:.1f} · '
                    f"{_t(maturity.maturity_for(framework.score))}</font>",
                    S["h2"],
                ),
                FRAMEWORK_BLOCK.get(framework.framework, PASTEL_SKY),
                width,
            )
        )
        rows: list[list] = [
            [
                "",
                Paragraph("PILLAR", S["eyebrow"]),
                Paragraph("SCORE", S["eyebrow"]),
                Paragraph("MATURITY", S["eyebrow"]),
                Paragraph("PASSED", S["eyebrow"]),
                Paragraph("STATUS", S["eyebrow"]),
            ]
        ]
        for pillar in framework.pillars:
            evaluated = pillar.checks_evaluated > 0
            rows.append(
                [
                    Swatch(pillar.score, evaluated),
                    Paragraph(_t(pillar.pillar_name), S["cell_b"]),
                    Paragraph("—" if not evaluated else f"{pillar.score:.0f}", S["cell"]),
                    Paragraph(
                        "Not evaluated"
                        if not evaluated
                        else _t(maturity.maturity_for(pillar.score)),
                        S["cell"],
                    ),
                    Paragraph(
                        "—"
                        if not evaluated
                        else f"{pillar.checks_passed}/{pillar.checks_evaluated}",
                        S["cell"],
                    ),
                    Paragraph(
                        "Not applicable to this design"
                        if not evaluated
                        else (
                            f"{pillar.checks_total - pillar.checks_evaluated} of "
                            f"{pillar.checks_total} n/a"
                            if pillar.checks_evaluated < pillar.checks_total
                            else "All checks applied"
                        ),
                        S["small"],
                    ),
                ]
            )

        table = Table(
            rows,
            colWidths=[
                10 * mm,
                width - 10 * mm - 16 * mm - 26 * mm - 18 * mm - 38 * mm,
                16 * mm,
                26 * mm,
                18 * mm,
                38 * mm,
            ],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, NAVY),
                    ("LINEBELOW", (0, 1), (-1, -2), 0.4, HAIRLINE),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, OFF_WHITE]),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 4 * mm))

    story.extend(_not_applicable_note(result, width))
    return story


def _not_applicable_note(result: ReviewResult, width: float) -> list:
    """Why a pillar reads "Not applicable to this design".

    The STATUS column can only fit a conclusion, and a conclusion with no argument is
    not auditable — which matters most here, because eighteen of the forty-five checks
    turn on whether the design has an AI/ML component, so a wholly-skipped pillar is
    one of the largest single influences on the score in this document.

    So the reasoning goes underneath, once, with the component labels that were
    searched. Nothing is recomputed: this prints the record the review already stores,
    the same sentence the results page shows.

    Absent entirely when every pillar was evaluated — there is nothing to explain.
    """
    skipped = [
        pillar
        for framework in result.frameworks
        for pillar in framework.pillars
        if pillar.checks_evaluated == 0
    ]
    if not skipped:
        return []

    detection = result.ai_detection
    names = ", ".join(_t(pillar.pillar_name) for pillar in skipped)
    body = (
        f'<b>{names}</b> {"was" if len(skipped) == 1 else "were"} not evaluated. '
        f"{_t(detection.rationale)}"
    )

    # The disagreement case: evidence of AI on a design whose AI pillar was skipped.
    # Stated plainly rather than resolved, because nothing in the pipeline overrides a
    # verdict on the strength of a keyword match.
    if any(detection.disagrees_with_pillar(pillar) for pillar in skipped):
        body += (
            " <b>These two statements disagree.</b> The evidence above points to an "
            "AI/ML component while the checks were scored as not applicable. Nothing "
            "was changed automatically; both are reported so a reviewer can judge."
        )

    return [
        _pastel_block(Paragraph(body, S["small"]), PASTEL_CREAM, width),
        Spacer(1, 4 * mm),
    ]


def _findings(result: ReviewResult) -> list:
    width = PAGE_W - 2 * MARGIN
    open_findings = [f for f in result.findings if f.status in ("fail", "partial")]
    story: list = [
        PageBreak(),
        Paragraph("Findings and remediation", S["h1"]),
        Paragraph(
            f"{len(open_findings)} open of {len(result.findings)} checks, grouped by "
            "severity and ordered by remediation priority within each group.",
            S["muted"],
        ),
        Spacer(1, 4 * mm),
    ]

    if not open_findings:
        story.append(
            Paragraph("No gaps found — every applicable check passed.", S["body"])
        )
        return story

    for severity in SEVERITY_ORDER:
        group = [f for f in open_findings if f.severity == severity]
        if not group:
            continue
        group.sort(key=lambda f: (f.priority == 0, f.priority))
        story.append(Spacer(1, 3 * mm))
        story.append(
            KeepTogether(
                [
                    Paragraph(
                        f"{_t(SEVERITY_HEADING[severity])} "
                        f'<font color="#47525E">({len(group)})</font>',
                        S["h2"],
                    ),
                    HRule(width, NAVY, 0.8),
                ]
            )
        )
        for finding in group:
            story.append(_finding_block(finding, width))

    met = [f for f in result.findings if f.status == "pass"]
    na = [f for f in result.findings if f.status == "not_applicable"]
    story.append(Spacer(1, 6 * mm))
    story.append(HRule(width))
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            f"<b>{len(met)} checks met</b> and <b>{len(na)} not applicable</b>, "
            "excluded from the scored denominator rather than counted as failures.",
            S["small"],
        )
    )
    return story


def _finding_block(finding: Finding, width: float) -> Flowable:
    rank = str(finding.priority) if finding.priority > 0 else "·"
    parts: list = [
        Paragraph(
            f'<font color="#1420BE"><b>{_t(rank)}</b></font>&nbsp;&nbsp;'
            f"<b>{_t(finding.title)}</b>",
            S["h3"],
        ),
        Paragraph(
            f"{_t(STATUS_LABEL.get(finding.status, finding.status))} · "
            f"{_t(finding.severity)} severity · "
            f"{_t(finding.pillar_id.replace('_', ' '))} · "
            f"{_t(finding.check_id)}",
            S["small"],
        ),
    ]
    if finding.evidence:
        parts += [Spacer(1, 1.5 * mm), Paragraph(_t(finding.evidence), S["body"])]

    if finding.remediation:
        effort = (
            f" · {_t(finding.remediation_effort)} effort"
            if finding.remediation_effort
            else ""
        )
        remediation = Table(
            [
                [
                    [
                        Paragraph(f"REMEDIATION{effort}", S["eyebrow"]),
                        Spacer(1, 1 * mm),
                        *_prose_or_list(finding.remediation, S["body"]),
                    ]
                ]
            ],
            colWidths=[width - 4 * mm],
        )
        remediation.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), OFF_WHITE),
                    ("LINEBEFORE", (0, 0), (0, -1), 1.4, ACCENT),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                    ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
                ]
            )
        )
        parts += [Spacer(1, 2 * mm), remediation]

    if finding.affected_components:
        parts += [
            Spacer(1, 1.5 * mm),
            Paragraph(
                "Affects: " + _t(", ".join(finding.affected_components)), S["small"]
            ),
        ]

    parts.append(Spacer(1, 5 * mm))
    # Keeping a finding whole stops a remediation box being orphaned from its title.
    return KeepTogether(parts)


def _roadmap(result: ReviewResult) -> list:
    """How to improve — the open findings sequenced into phases by effort.

    The same grouping the results view renders, from the same rule in `roadmap.py`,
    so the report and the screen cannot disagree about a finding's phase.

    Titles and remediation text are reproduced verbatim. This section restates the
    findings in a different order rather than adding anything to them, so it stays
    compact: one line of remediation per finding, with the full evidence left to the
    findings section that follows.
    """
    width = PAGE_W - 2 * MARGIN
    grouped = roadmap.group_by_phase(result.findings)
    total = sum(len(grouped[phase]) for phase in roadmap.PHASE_ORDER)
    if total == 0:
        return []

    story: list = [
        PageBreak(),
        Paragraph("How to improve", S["h1"]),
        Paragraph(
            f"{total} open {'finding' if total == 1 else 'findings'} sequenced by "
            "implementation effort and blast radius. Phases are assigned from the "
            "recorded remediation effort and the number of components each fix "
            "touches — the same rule the dashboard applies.",
            S["muted"],
        ),
        Spacer(1, 4 * mm),
    ]

    for phase in roadmap.PHASE_ORDER:
        group = grouped[phase]
        story.append(Spacer(1, 3 * mm))
        # The heading is kept with the blurb and rule so a phase never opens a page
        # on its own, and an empty phase still appears: "Structural (0)" is a result.
        story.append(
            KeepTogether(
                [
                    Paragraph(
                        f"{_t(roadmap.PHASE_LABEL[phase])} "
                        f'<font color="#47525E">({len(group)})</font>',
                        S["h2"],
                    ),
                    Paragraph(_t(roadmap.PHASE_BLURB[phase]), S["small"]),
                    HRule(width, NAVY, 0.8),
                    # Without this the first title butts against the rule and reads as
                    # part of the heading block rather than as the first entry.
                    Spacer(1, 2.5 * mm),
                ]
            )
        )
        if not group:
            story += [
                Spacer(1, 2 * mm),
                Paragraph("Nothing in this phase.", S["muted"]),
            ]
            continue
        for finding in group:
            story.append(_roadmap_row(finding, width))

    return story


def _roadmap_row(finding: Finding, width: float) -> Flowable:
    """One phase entry: title, verbatim remediation, and the two phase inputs.

    The effort and component count are shown because they are what put the finding in
    this phase — a reviewer disagreeing with the placement can see why without
    reading the rule.
    """
    facts = [_t(finding.severity) + " severity", _t(finding.pillar_id.replace("_", " "))]
    if finding.remediation_effort:
        facts.append(f"{_t(finding.remediation_effort)} effort")
    if finding.affected_components:
        count = len(finding.affected_components)
        facts.append(f"{count} component" + ("s" if count != 1 else ""))

    return KeepTogether(
        [
            Paragraph(f"<b>{_t(finding.title)}</b>", S["h3"]),
            Paragraph(" · ".join(facts), S["small"]),
            Spacer(1, 1.5 * mm),
            Paragraph(
                _t(finding.remediation)
                if finding.remediation
                else "No remediation text was generated for this check.",
                S["body"],
            ),
            Spacer(1, 4 * mm),
        ]
    )


def _summaries(result: ReviewResult) -> list:
    width = PAGE_W - 2 * MARGIN
    story: list = []
    if result.executive_summary:
        # Cream block, the same treatment the dashboard gives this paragraph.
        story += [
            Paragraph("Executive summary", S["h1"]),
            Spacer(1, 2 * mm),
            _pastel_block(
                Paragraph(_t(result.executive_summary), S["body"]),
                PASTEL_CREAM,
                width,
            ),
            Spacer(1, 6 * mm),
        ]
    if result.summary:
        story += [
            Paragraph("Assessment", S["h2"]),
            *_prose_or_list(result.summary, S["body"]),
            Spacer(1, 6 * mm),
        ]
    if result.delta:
        delta = result.delta
        direction = "improved" if delta.change > 0 else "declined" if delta.change < 0 else "unchanged"
        story += [
            Paragraph("Change since the previous review", S["h2"]),
            Paragraph(
                f"{delta.previous_overall_score:.1f} → "
                f"{delta.current_overall_score:.1f} "
                f"({_t(direction)} {abs(delta.change):.1f}) · "
                f"{len(delta.resolved_checks)} resolved · "
                f"{len(delta.new_checks)} new · "
                f"{len(delta.unchanged_failures)} still open",
                S["body"],
            ),
            Spacer(1, 6 * mm),
        ]
    return story


def _appendix(result: ReviewResult, diagram: tuple[str, bytes] | None) -> list:
    """The uploaded diagram, embedded when it is an image.

    A draw.io upload is XML with no rendered form, so rather than embed nothing
    the appendix lists the components the deterministic parser extracted from it —
    which is what the review actually reasoned over.
    """
    width = PAGE_W - 2 * MARGIN
    story: list = [
        PageBreak(),
        Paragraph("Appendix — architecture input", S["h1"]),
    ]

    filename = diagram[0] if diagram else ""
    suffix = pathlib.Path(filename.lower()).suffix

    if diagram and suffix in IMAGE_SUFFIXES:
        story.append(Paragraph(f"Uploaded diagram: {_t(filename)}", S["muted"]))
        story.append(Spacer(1, 4 * mm))
        try:
            reader = ImageReader(io.BytesIO(diagram[1]))
            iw, ih = reader.getSize()
            avail_h = PAGE_H - 2 * MARGIN - 40 * mm
            scale = min(width / iw, avail_h / ih, 1.0)
            story.append(_DrawnImage(reader, iw * scale, ih * scale))
        except Exception as exc:  # noqa: BLE001 — a bad upload must not fail the export
            story.append(
                Paragraph(
                    f"The uploaded image could not be embedded "
                    f"({_t(type(exc).__name__)}). The review itself is unaffected.",
                    S["small"],
                )
            )
    elif diagram:
        story.append(
            Paragraph(
                f"Uploaded diagram: {_t(filename)} — draw.io XML, parsed "
                "deterministically rather than rendered. The components below are "
                "what the review reasoned over.",
                S["muted"],
            )
        )
    else:
        story.append(
            Paragraph(
                "No architecture diagram was retained for this review.", S["muted"]
            )
        )

    if result.components:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("Components identified", S["h2"]))
        rows: list[list] = [
            [Paragraph(h, S["eyebrow"]) for h in ("COMPONENT", "KIND", "PROVIDER", "SERVICE")]
        ]
        for component in result.components:
            rows.append(
                [
                    Paragraph(_t(component.label or component.id), S["cell_b"]),
                    Paragraph(_t(component.kind), S["cell"]),
                    Paragraph(_t(component.provider), S["cell"]),
                    Paragraph(_t(component.service or "—"), S["cell"]),
                ]
            )
        table = Table(
            rows,
            colWidths=[width - 32 * mm - 24 * mm - 34 * mm, 32 * mm, 24 * mm, 34 * mm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, NAVY),
                    ("LINEBELOW", (0, 1), (-1, -2), 0.4, HAIRLINE),
                ]
            )
        )
        story.append(table)

    return story


class _DrawnImage(Flowable):
    """Draws a pre-scaled ImageReader. Avoids re-decoding on every layout pass."""

    def __init__(self, reader: ImageReader, width: float, height: float) -> None:
        super().__init__()
        self._reader, self.width, self.height = reader, width, height

    def draw(self) -> None:
        self.canv.drawImage(self._reader, 0, 0, self.width, self.height, mask="auto")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def build_pdf(
    result: ReviewResult, diagram: tuple[str, bytes] | None = None
) -> bytes:
    """Render a finished review as a PDF.

    `diagram` is an optional `(filename, bytes)` pair for the appendix. Passing
    None simply omits the embedded image; the appendix still lists components.
    """
    buffer = io.BytesIO()
    doc = _document(buffer, result.title)

    story: list = []
    story += _cover(result)
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())
    story += _summaries(result)
    story += _scorecard(result)
    story += _roadmap(result)
    story += _findings(result)
    story += _appendix(result, diagram)

    doc.build(story)
    return buffer.getvalue()


def filename_for(result: ReviewResult) -> str:
    """A filesystem-safe download name derived from the review title."""
    stem = "".join(
        ch if ch.isalnum() or ch in "-_" else "-"
        for ch in (result.title or "review").strip().lower().replace(" ", "-")
    )
    stem = "-".join(part for part in stem.split("-") if part)[:60] or "review"
    return f"trust7-{stem}.pdf"


def _format_date(iso: str) -> str:
    """`2026-07-27T10:04:00Z` -> `27 July 2026`, falling back to the raw string."""
    import datetime

    try:
        parsed = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso or "date unknown"
    return f"{parsed.day} {parsed.strftime('%B %Y')}"
