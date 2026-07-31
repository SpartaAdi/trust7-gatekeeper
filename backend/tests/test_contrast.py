"""WCAG AA contrast, asserted rather than eyeballed.

This exists because a contrast miss shipped: `ACCENT_ON_DARK` was picked by eye to
fix an unreadable cover, looked fine, and measured 4.12:1 — passing for the 13pt
band it was checked against and failing for the 8.5pt eyebrow beside it. Nothing in
the suite noticed, because the tests only knew the hex had changed.

The audit itself lives in `scripts/contrast_audit.py` and parses the tokens out of
`frontend/src/index.css` and `backend/report.py` rather than restating them, so this
cannot drift into asserting yesterday's palette was fine.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import contrast_audit  # noqa: E402


def test_every_rendered_pair_clears_wcag_aa() -> None:
    failures, _ = contrast_audit.audit()

    assert not failures, "AA failures:\n" + "\n".join(
        f"  {ratio:.2f}:1 (need {need}) {label}" for label, ratio, need in failures
    )


def test_the_audit_covers_the_whole_surface_area() -> None:
    """A passing audit means nothing if the pair list has been quietly trimmed."""
    pairs = contrast_audit.pairs()
    sections = {section for section, *_ in pairs}

    assert len(pairs) >= 130, f"only {len(pairs)} pairs — did the list shrink?"
    assert sections >= {
        "Findings accordion",
        "Maturity tooltip",
        "Top action items",
        "Score badge",
        "Pastel blocks",
        "Header and nav",
        "Controls",
        # Added by the visual pass over these three surfaces. They had never been
        # audited, and the gap was not academic: the version chips' entire boundary
        # was a hairline at 1.42:1 and the feedback textarea's at 1.38:1.
        "AI detection panel",
        "Version banner and delta",
        "Feedback box",
        "PDF cover",
        "PDF body",
    }


def test_borderline_pairs_are_known_and_few() -> None:
    """Passing with under 15% margin is worth knowing about, not worth failing on.

    Pinning the list means a NEW borderline pair shows up as a test failure and gets
    a decision, rather than accumulating silently until the next drift pushes it
    under 4.5. Every entry here needs a component change, not a token change.

    The two AI-detection tags were added by the visual pass over that panel and are
    accepted deliberately. `sev-medium` on its own 8% tint at 4.73 is the same
    treatment the findings accordion's "Partial" tag already ships at 5.03 — a
    component-wide decision, not a new one. `ink-faint` at 5.04 is the quiet tag for
    the ordinary "no AI here" case, and darkening it would make a non-finding shout.
    Neither is colour-only: the tag's own word carries the verdict.
    """
    _, borderline = contrast_audit.audit()

    assert {label for label, *_ in borderline} == {
        "Findings accordion: status tag 'Partial', sev-medium on sev-medium/8",
        "Header and nav: inactive tab, white/50 on navy",
        "AI detection panel: verdict tag 'AI likely', sev-medium on sev-medium/8 over sunken",
        "AI detection panel: verdict tag 'No AI found', ink-faint on ink-faint/5 over sunken",
    }


@pytest.mark.parametrize(
    "token, surfaces",
    [
        # A token used on several surfaces has to clear the WORST one. Averaging is
        # how `ink-muted` passed on mint (4.56) while failing on sky (4.09).
        ("ink-muted", ("surface", "surface-sunken", "pastel-sky", "pastel-mint",
                       "pastel-tan", "pastel-cream", "pastel-teal")),
        ("ink-faint", ("surface", "surface-sunken", "pastel-sky", "pastel-mint",
                       "pastel-tan", "pastel-cream", "pastel-teal")),
        ("ink", ("surface", "surface-sunken", "pastel-sky", "pastel-mint",
                 "pastel-tan", "pastel-cream", "pastel-teal")),
    ],
)
def test_a_multi_surface_token_clears_aa_on_its_worst_surface(
    token: str, surfaces: tuple[str, ...]
) -> None:
    web = contrast_audit.web_tokens()
    worst = min(
        (contrast_audit.ratio(web[token], web[surface]), surface)
        for surface in surfaces
    )

    assert worst[0] >= 4.5, f"{token} is {worst[0]:.2f}:1 on {worst[1]}"


def test_the_ink_tiers_stay_visually_distinct() -> None:
    """AA is the floor, not the goal: three tiers all at 4.6:1 would be compliant
    and useless, because nothing would read as secondary."""
    web = contrast_audit.web_tokens()
    on_white = [
        contrast_audit.ratio(web[name], web["surface"])
        for name in ("ink", "ink-muted", "ink-faint")
    ]

    assert on_white == sorted(on_white, reverse=True), "tier order inverted"
    for stronger, weaker in zip(on_white, on_white[1:]):
        assert stronger / weaker >= 1.30, (
            f"tiers {stronger:.2f} and {weaker:.2f} are too close to read as a hierarchy"
        )


def test_the_three_brand_anchors_are_unchanged() -> None:
    """The retheme moved everything else; these three are the brand and are fixed."""
    web = contrast_audit.web_tokens()

    assert web["minfy-navy"] == "#1b263b"
    assert web["minfy-indigo"] == "#1420be"
    assert web["minfy-blue"] == "#1c55bb"
    assert web["minfy-yellow"] == "#fdc921"


# The superseded brand accent, assembled rather than written out: a test that greps
# the tree for a literal must not contain that literal, or it only ever finds itself.
SUPERSEDED_ORANGE = "e8" + "5d" + "26"


def test_the_superseded_orange_is_gone_from_every_source() -> None:
    """The old brand accent. Zero occurrences anywhere, not just absent from the
    palette — a stale hex in a doc or a fixture is still a wrong brand colour."""
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {
            ".py", ".ts", ".tsx", ".css", ".json", ".md", ".html", ".yaml", ".yml"
        }:
            continue
        if any(part in {"node_modules", "dist", ".git", "__pycache__", "local-data"}
               for part in path.parts):
            continue
        text = path.read_text(errors="ignore").lower()
        if SUPERSEDED_ORANGE in text:
            offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, f"superseded Minfy orange still present in: {offenders}"
