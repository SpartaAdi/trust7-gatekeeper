"""Score-to-maturity banding.

Deliberately mirrors `frontend/src/maturity.ts`. The bands are duplicated across
the language boundary rather than served from an endpoint, because the UI needs
them synchronously to render and a round trip for five constants is not worth a
request. `tests/test_report.py` asserts the boundaries here match the documented
values, so the two drifting apart fails a test rather than silently disagreeing
between the screen and the exported PDF.
"""

from __future__ import annotations

# (minimum score, label) — highest band first, same order as the TS file.
BANDS: tuple[tuple[float, str], ...] = (
    (90, "Pioneering"),
    (75, "Certified"),
    (60, "Governed"),
    (40, "Managed"),
    (0, "Aware"),
)


def maturity_for(score: float) -> str:
    for minimum, label in BANDS:
        if score >= minimum:
            return label
    return "Aware"
