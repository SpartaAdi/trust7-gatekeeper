"""Find the architecture diagram embedded inside a PDF solution document.

`documents.extract_text` reads `page.extract_text()` and nothing else, so a diagram
that exists only as a picture inside a SoW has always been invisible to the
pipeline — not mis-read, not timed out, simply never looked at. On a real signed
SoW that meant the design was scored on its prose alone while its target
architecture sat on page 8 as a 2836x1699 image nothing opened.

This is a SELECTION problem, not an extraction one. A real SoW carries logos, a
cover collage and a signature scan alongside the diagram, and picking the wrong one
costs a vision call and fills the review with components that are not in the design.
So two independent signals decide it, and neither is trusted alone:

* **Area**, which cheaply eliminates furniture. Logos are small in a way diagrams
  never are.
* **A diagram keyword on the image's OWN page**, which is what separates a large
  decorative image from a large meaningful one. A cover collage can be bigger than
  the diagram; the page it sits on does not say "architecture".

Nothing here is tuned to one document. The keyword list is short and generic, the
threshold is an order of magnitude below a real diagram and twice the largest real
logo, and the ranking works whichever way the ambiguous cases fall — see
`select_diagram`.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Words that mark a page as carrying the design rather than the paperwork.
#
# Deliberately short and generic. Every one of these is how a solution document
# introduces its architecture regardless of who wrote it, and a longer list tuned to
# one client's section headings would score that client's SoW well and every other
# one worse. Matched case-insensitively against the page's own extracted text.
DIAGRAM_KEYWORDS = (
    "architecture",
    "diagram",
    "topology",
    "target state",
    "design overview",
)

# Pixel area below which an embedded image is page furniture, not a diagram.
#
# 400,000 px is about 633x633. The calibration comes from a real signed SoW: its two
# logos are 453x132 (59,796 px) and 784x216 (169,344 px), and its architecture
# diagram is 2836x1699 (4,818,364 px). So this sits at 2.4x the largest real logo and
# an order of magnitude below the real diagram — wide margins on both sides, because
# the cost of being wrong is asymmetric. Too low wastes a vision call on a letterhead
# and injects its non-components into the review; too high silently drops a diagram
# exported at modest resolution, which is the failure this module exists to fix.
#
# Area, not bytes. The same 400,000-pixel diagram can be 40 KB or 4 MB depending on
# how it was encoded, and the cover collage on that real SoW is the largest image in
# the file by bytes while being decoration.
MIN_DIAGRAM_AREA_PX = 400_000

# How many embedded diagrams are read. One.
#
# This is the latency and cost bound: at most ONE extra vision call, and only when a
# document-only upload actually contains a qualifying image. A SoW with no embedded
# images does no extra work at all — the page walk is local and the call never
# happens. Raising this multiplies vision calls per review, so it is a constant with
# a comment rather than a number inline.
MAX_EMBEDDED_DIAGRAMS = 1

# Formats the diagram path already accepts, so a passthrough needs no re-encoding.
# Anything else — JPEG 2000 and TIFF both turn up in PDFs — is converted to PNG
# rather than rejected, because the format an authoring tool happened to embed says
# nothing about whether the picture is the architecture.
_PASSTHROUGH = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif",
                "WEBP": "image/webp"}


@dataclass(frozen=True)
class EmbeddedImage:
    """One image found inside a PDF, with what is known about where it sat."""

    page: int
    name: str
    width: int
    height: int
    keyword: str
    data: bytes
    media_type: str

    @property
    def area(self) -> int:
        return self.width * self.height


def _page_keyword(text: str) -> str:
    lowered = " ".join((text or "").lower().split())
    for word in DIAGRAM_KEYWORDS:
        if word in lowered:
            return word
    return ""


def _survey(reader) -> list[EmbeddedImage]:
    """Every embedded image, measured but NOT decoded.

    `Image.open` parses the header and stops, so this reads dimensions without
    pulling pixels into memory. That matters on the free tier: a single 2836x1699
    RGB bitmap is roughly 14 MB decoded, and a cover collage plus a diagram plus a
    signature scan decoded together is how a 512 MB instance dies mid-review. Only
    the one selected image is ever fully decoded, and only if it needs converting.
    """
    from PIL import Image

    found: list[EmbeddedImage] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            images = list(page.images)
        except Exception as exc:  # noqa: BLE001 — one bad XObject must not end the walk
            log.warning("Could not list images on page %d: %s", number, exc)
            continue

        keyword = ""
        try:
            keyword = _page_keyword(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 — text is a signal, not a requirement
            log.warning("Could not read text on page %d: %s", number, exc)

        for image in images:
            data = image.data
            try:
                with Image.open(io.BytesIO(data)) as handle:
                    width, height = handle.size
                    fmt = (handle.format or "").upper()
            except Exception as exc:  # noqa: BLE001 — an undecodable image is skipped
                log.info(
                    "Skipping an undecodable image on page %d (%s): %s",
                    number, getattr(image, "name", "?"), exc,
                )
                continue
            found.append(
                EmbeddedImage(
                    page=number,
                    name=getattr(image, "name", "") or "",
                    width=width,
                    height=height,
                    keyword=keyword,
                    data=data,
                    media_type=_PASSTHROUGH.get(fmt, ""),
                )
            )
    return found


def _as_supported_image(candidate: EmbeddedImage) -> EmbeddedImage | None:
    """Return the candidate in a format the vision path accepts, converting if needed."""
    if candidate.media_type:
        return candidate

    from PIL import Image

    try:
        with Image.open(io.BytesIO(candidate.data)) as handle:
            converted = handle.convert("RGB")
            buffer = io.BytesIO()
            converted.save(buffer, format="PNG")
    except Exception as exc:  # noqa: BLE001 — a diagram we cannot convert is not fatal
        log.warning(
            "Could not convert the embedded image on page %d to PNG: %s",
            candidate.page, exc,
        )
        return None

    from dataclasses import replace

    return replace(candidate, data=buffer.getvalue(), media_type="image/png")


def select_diagram(pdf_bytes: bytes) -> EmbeddedImage | None:
    """The one embedded image most likely to be the architecture diagram, or None.

    Ranking is keyword first, then area — and the order matters more than it looks.
    On the real SoW the cover collage (1622x2100) is decoration and the diagram
    (2836x1699) is the design, and the diagram wins under EITHER reading of the cover
    page: if page 1's text has no diagram keyword the diagram wins on the keyword; if
    it does, they tie on the keyword and the diagram wins on area. A rule that
    depended on which of those is true would be a rule tuned to one file.

    Returns None, cheaply and often: not a PDF, no images, or nothing above the area
    floor. That is the common case for a text-only SoW and it costs no model call.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            return None
    except Exception as exc:  # noqa: BLE001 — `documents.extract_text` already
        # reported anything genuinely wrong with this file; a diagram hunt failing
        # on it must not turn a readable document into a failed review.
        log.info("Not searching for an embedded diagram: %s", exc)
        return None

    candidates = [image for image in _survey(reader) if image.area >= MIN_DIAGRAM_AREA_PX]
    if not candidates:
        return None

    candidates.sort(key=lambda image: (bool(image.keyword), image.area), reverse=True)
    for candidate in candidates[:MAX_EMBEDDED_DIAGRAMS]:
        usable = _as_supported_image(candidate)
        if usable is None:
            continue
        log.info(
            "Selected the embedded image on page %d (%dx%d, %s) as the architecture "
            "diagram: keyword=%r, %d candidate(s) above the area floor.",
            usable.page, usable.width, usable.height, usable.name or "unnamed",
            usable.keyword, len(candidates),
        )
        return usable
    return None
