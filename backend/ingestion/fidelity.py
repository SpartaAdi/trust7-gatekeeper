"""Two data-fidelity measurements taken at ingestion, and never blended.

`structural_coverage` is EXACT — a draw.io file enumerates its own elements, so
both sides of the ratio are counted rather than estimated, and no model is
involved. `ocr_coverage_proxy` is an ESTIMATE, and a weak one on purpose: it
compares the vision model's reading of an image against a second fallible
reader's, because nothing here knows what is really in an image.

They are separate functions returning separate models with no arithmetic between
them, and `schema.DataFidelity` has no composite field. That is deliberate — see
the note above `COVERAGE_REVIEW_THRESHOLD` in schema.py for why averaging an exact
ratio with an estimate produces a number that looks measured and is not.

The third fidelity number, the grounding filter's catch count, is not here: it is
produced by the remediate stage, which is the only place that sees the claims
being filtered. See `agent/stages.py::_use_case_notes`.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from schema import DesignGraph, OcrCoverageProxy, StructuralCoverage

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 1. Structural extraction coverage — draw.io only, exact, no model call
# --------------------------------------------------------------------------- #

# Diagram elements are cells carrying `vertex="1"` or `edge="1"`, plus the
# `<object>` / `<UserObject>` wrappers draw.io uses when a shape has custom
# properties.
#
# draw.io's root and layer cells — conventionally `id="0"` and `id="1"` — are
# excluded, and that exclusion is the difference between a usable metric and a
# useless one. Every mxGraphModel contains exactly those two, they carry neither
# attribute, they are never diagram content, and counting them would cap a
# perfectly parsed 11-element diagram at 11/13 = 84.6% and fire the
# review-recommended threshold on every upload forever.
_VERTEX = re.compile(r'<mxCell\b[^>]*\bvertex="1"[^>]*>')
_EDGE = re.compile(r'<mxCell\b[^>]*\bedge="1"[^>]*>')
_WRAPPER = re.compile(r"<(?:object|UserObject)\b[^>]*>")
_HAS_LABEL = re.compile(r'\bvalue="[^"]+"')
_HAS_WRAPPER_LABEL = re.compile(r'\blabel="[^"]+"')
_ID = re.compile(r'\bid="([^"]*)"')

# A compressed export cannot be counted without inflating it a second time, and a
# wrong denominator is worse than no metric. `drawio.parse` has already done that
# work; this declines rather than duplicating it.
_UNCOMPRESSED_MARKER = "mxGraphModel"


def structural_coverage(graph: DesignGraph, raw: bytes) -> StructuralCoverage | None:
    """Parsed graph elements over diagram elements in the XML. None if not countable.

    Exact on both sides. The numerator counts notes as well as components and
    connections, because a shape that became an annotation was understood — it just
    is not a component.

    `dropped` names what did not survive, ordered most-common first, because the
    percentage alone is not actionable: 82% because six unlabelled decorative
    shapes were skipped is correct behaviour, and 82% because a third of the file
    used a construct the parser does not know is a bug. Both look identical without
    the breakdown.
    """
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — a measurement, never a failure
        return None

    if _UNCOMPRESSED_MARKER not in text:
        return None

    vertices = _VERTEX.findall(text)
    edges = _EDGE.findall(text)
    wrappers = _WRAPPER.findall(text)
    total = len(vertices) + len(edges) + len(wrappers)
    if not total:
        return None

    parsed = len(graph.components) + len(graph.connections) + len(graph.notes)

    # Clamped, and the clamp is load-bearing rather than defensive. The two sides
    # are counted by different code — a regex here, a real XML parse in
    # drawio.py — so they can legitimately disagree at the margins: a `<object>`
    # wrapper and its inner `mxCell` are one element to the parser and could be
    # two matches here. A coverage figure above 100% would destroy trust in the
    # number far more than the rounding it hides.
    percent = round(min(100.0, 100.0 * parsed / total), 1)

    return StructuralCoverage(
        parsed_elements=parsed,
        total_elements=total,
        percent=percent,
        dropped=_dropped_reasons(graph, text, vertices, edges, parsed, total),
    )


def _dropped_reasons(
    graph: DesignGraph,
    text: str,
    vertices: list[str],
    edges: list[str],
    parsed: int,
    total: int,
) -> list[str]:
    """Why elements did not reach the graph, counted, most common first.

    Each reason is derived independently from the XML rather than by subtracting
    counts, so the list stays honest when they do not add up exactly. An
    `unaccounted` entry appears when they do not — better a visible remainder than
    a breakdown that silently absorbs it.
    """
    reasons: Counter[str] = Counter()

    unlabelled = sum(1 for cell in vertices if not _HAS_LABEL.search(cell))
    if unlabelled:
        # Not a defect. `drawio.parse` drops these on purpose — a real diagram is
        # full of arrows, containers and decoration carrying no reviewable meaning
        # — and the reason says so, so a low percentage explains itself.
        reasons["unlabelled shapes, which carry no reviewable meaning"] = unlabelled

    ids = [match.group(1) for cell in vertices if (match := _ID.search(cell))]
    duplicates = sum(count - 1 for count in Counter(ids).values() if count > 1)
    if duplicates:
        reasons["shapes sharing an id with an earlier shape"] = duplicates

    kept = {component.id for component in graph.components}
    dangling = 0
    for edge in edges:
        source = re.search(r'\bsource="([^"]*)"', edge)
        target = re.search(r'\btarget="([^"]*)"', edge)
        if not source or not target:
            dangling += 1
        elif source.group(1) not in kept or target.group(1) not in kept:
            dangling += 1
    if dangling:
        reasons["connections whose endpoints are not components"] = dangling

    accounted = sum(reasons.values())
    remainder = total - parsed - accounted
    if remainder > 0:
        reasons["unaccounted — a construct this parser does not recognise"] = remainder

    return [
        f"{count} {reason}" for reason, count in reasons.most_common() if count > 0
    ]


# --------------------------------------------------------------------------- #
# 2. OCR coverage proxy — image only, an ESTIMATE against a second fallible reader
# --------------------------------------------------------------------------- #

# Words shorter than this are dropped from both sides. OCR produces a great deal
# of one- and two-character noise from arrow heads, borders and icon fragments,
# and matching on it would swamp the signal in both directions.
MIN_TOKEN_CHARS = 3

# Tokens that carry no identifying information, so a match on them means nothing.
# Kept small on purpose: an aggressive stop list would quietly raise the score by
# removing exactly the words most likely to be missed.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "into", "via", "not", "all", "any",
        "this", "that", "these", "those", "are", "was", "were", "has", "have",
        "per", "own",
    }
)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]*")

# How many unmatched tokens to keep as a sample. A handful is enough to tell a
# missed label from OCR noise by eye; the full list would be mostly noise.
MAX_UNMATCHED_SAMPLE = 8


def _tokens(text: str) -> set[str]:
    """Lowercased tokens worth comparing, deduplicated.

    A SET, not a list, so the ratio is over distinct words rather than word
    instances. Instance counting would let one repeated label dominate the figure
    — a legend repeating "AWS" nine times would move the score more than nine
    genuinely distinct missed components.
    """
    return {
        token
        for token in _TOKEN.findall(text.lower())
        if len(token) >= MIN_TOKEN_CHARS and token not in _STOPWORDS
    }


def graph_text(graph: DesignGraph) -> str:
    """Everything the graph says, as one string to match OCR tokens against.

    Every field a transcribed word could legitimately have landed in — labels,
    ids, service and provider names, edge labels and protocols, and notes. Using
    all of them rather than labels alone is deliberate: a word OCR read that the
    model recorded as a protocol or filed in `notes` WAS extracted, and counting it
    as missed would understate coverage.
    """
    parts: list[str] = []
    for component in graph.components:
        parts += [component.id, component.label, component.kind,
                  component.provider, component.service]
        parts += [f"{k} {v}" for k, v in component.attributes.items()]
    for connection in graph.connections:
        parts += [connection.source_id, connection.target_id,
                  connection.label, connection.protocol]
    parts += graph.notes
    return " ".join(part for part in parts if part)


def ocr_available() -> tuple[bool, str]:
    """Whether an OCR engine is usable here, and why not if it is not.

    Reported rather than assumed, because it is genuinely absent in production:
    Render's native Python runtime installs from requirements.txt and cannot
    `apt-get install tesseract-ocr`, so this metric is unavailable on the deployed
    service until the service moves to a Docker runtime. `pytesseract` is a
    dev/harness dependency for that reason.

    An absent engine makes the metric ABSENT, never zero. A zero would read as "the
    vision model missed everything", which is a claim about the model rather than
    about our tooling.
    """
    try:
        import pytesseract
    except ImportError:
        return False, (
            "No OCR engine installed. This estimate needs pytesseract and the "
            "tesseract binary, which the deployed service does not carry — see "
            "ingestion/fidelity.py."
        )
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # noqa: BLE001 — pytesseract raises several types here
        return False, f"OCR engine not usable: {type(exc).__name__}: {exc}"
    return True, ""


def ocr_coverage_proxy(graph: DesignGraph, image: bytes) -> OcrCoverageProxy:
    """Fraction of OCR-read words that appear somewhere in the graph. AN ESTIMATE.

    Returns a model with `available=False` and a reason when OCR cannot run, and
    `is_estimate` is hard-wired True on every path so no caller can present this as
    a measurement. What a low number means is that two fallible readers disagree —
    not which of them is right. See `OcrCoverageProxy` for the full caveat.

    Any failure of the OCR pass itself is absorbed: this is a diagnostic on a
    review that has already succeeded, and it must never be the thing that breaks
    one.
    """
    available, reason = ocr_available()
    if not available:
        return OcrCoverageProxy(available=False, unavailable_reason=reason)

    try:
        import io

        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(image)) as opened:
            # Converted because Tesseract handles palette and alpha images poorly,
            # and a mode failure here would look like an unreadable diagram.
            text = pytesseract.image_to_string(opened.convert("RGB"))
    except Exception as exc:  # noqa: BLE001 — never break a completed review
        log.warning(
            "OCR coverage proxy could not run (%s: %s); reporting it as unavailable "
            "rather than as zero coverage.", type(exc).__name__, exc,
        )
        return OcrCoverageProxy(
            available=False,
            unavailable_reason=f"OCR pass failed: {type(exc).__name__}: {exc}",
        )

    ocr = _tokens(text)
    if not ocr:
        # OCR read nothing. That is not 0% coverage — it is no measurement, and
        # most often means a diagram carrying its meaning in shapes rather than
        # words, or an image OCR simply cannot read.
        return OcrCoverageProxy(
            available=False,
            unavailable_reason=(
                "OCR read no usable text from this image, so there is nothing to "
                "compare the extraction against. This is not a coverage figure of "
                "zero."
            ),
        )

    extracted = _tokens(graph_text(graph))
    # Substring matching as well as exact, because the two readers tokenise
    # differently on the same words: OCR splits "receipts-bucket" at the hyphen on
    # some renderings and not others, and an exact-only comparison would report a
    # miss for a word that is plainly present.
    haystack = " ".join(sorted(extracted))
    matched = {token for token in ocr if token in extracted or token in haystack}

    return OcrCoverageProxy(
        available=True,
        ocr_tokens=len(ocr),
        matched_tokens=len(matched),
        percent=round(100.0 * len(matched) / len(ocr), 1),
        sample_unmatched=sorted(ocr - matched)[:MAX_UNMATCHED_SAMPLE],
    )
