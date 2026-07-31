#!/usr/bin/env python3
"""WCAG AA contrast audit over every real colour pair the product renders.

Run it: `python3 scripts/contrast_audit.py` (add `--fail-only` for just the misses).
`backend/tests/test_contrast.py` runs the same check as a test, so a token change
that breaks AA fails the suite instead of waiting to be noticed by eye.

Two rules make this an audit rather than a table of nominal values:

1. **Tokens are parsed, never restated.** Web values come out of
   `frontend/src/index.css`'s `@theme` block and PDF values out of
   `backend/report.py`. A retheme therefore cannot leave this file behind saying
   the old numbers were fine.

2. **Translucent colours are composited.** `indigo/40`, `white/50`, `ink/15`,
   `sev-high/8` are flattened onto the surface actually behind them before
   measuring, because that is what an eye receives. Auditing the nominal token
   instead is how `ACCENT_ON_DARK` shipped at 4.12:1.

A pair is judged against the threshold for what it *is*: 4.5 for body text, 3.0
for large text (>=18.66px bold or >=24px) and for graphics that carry meaning.
Purely decorative rules and inactive controls are listed as EXEMPT with their
ratio shown — WCAG 1.4.11 and 1.4.3 exclude them, and hiding them would make the
exemption invisible rather than deliberate.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS = ROOT / "frontend" / "src" / "index.css"
REPORT = ROOT / "backend" / "report.py"


# --------------------------------------------------------------------------- #
# Colour maths
# --------------------------------------------------------------------------- #

def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    h = colour.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def ratio(foreground: str, background: str) -> float:
    a, b = luminance(foreground), luminance(background)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def over(foreground: str, background: str, alpha: float) -> str:
    """Flatten a translucent foreground onto its backdrop."""
    f, b = foreground.lstrip("#"), background.lstrip("#")
    out = []
    for i in (0, 2, 4):
        fv, bv = int(f[i : i + 2], 16), int(b[i : i + 2], 16)
        out.append(round(fv * alpha + bv * (1 - alpha)))
    return "#%02x%02x%02x" % tuple(out)


# --------------------------------------------------------------------------- #
# Token sources
# --------------------------------------------------------------------------- #

def web_tokens() -> dict[str, str]:
    """Every `--color-*` token in the `@theme` block of index.css."""
    css = CSS.read_text()
    block = css[css.index("@theme {") : css.index("\n}", css.index("@theme {"))]
    found = dict(re.findall(r"--color-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))
    if not found:
        raise SystemExit("no --color-* tokens found in index.css @theme block")
    return {name: value.lower() for name, value in found.items()}


def pdf_tokens() -> dict[str, str]:
    """The module-level `HexColor("#...")` constants in report.py."""
    source = REPORT.read_text()
    found = dict(
        re.findall(r"^([A-Z][A-Z_]*)\s*=\s*HexColor\(\"(#[0-9a-fA-F]{6})\"\)",
                   source, re.MULTILINE)
    )
    if not found:
        raise SystemExit("no HexColor constants found in report.py")
    return {name: value.lower() for name, value in found.items()}


# --------------------------------------------------------------------------- #
# The pairs, as the product actually composes them
# --------------------------------------------------------------------------- #

TEXT, LARGE, GRAPHIC, EXEMPT = "text", "large", "graphic", "exempt"
THRESHOLD = {TEXT: 4.5, LARGE: 3.0, GRAPHIC: 3.0}


def pairs() -> list[tuple[str, str, str, str, str]]:
    """(section, description, foreground, background, kind).

    Sections mirror where the pair appears, so a failure names a screen.
    """
    w = web_tokens()
    p = pdf_tokens()
    surface, sunken, navy = w["surface"], w["surface-sunken"], w["minfy-navy"]
    ink, muted, faint = w["ink"], w["ink-muted"], w["ink-faint"]
    indigo, blue, hairline = w["minfy-indigo"], w["minfy-blue"], w["hairline"]
    sky, mint, tan = w["pastel-sky"], w["pastel-mint"], w["pastel-tan"]
    cream, teal = w["pastel-cream"], w["pastel-teal"]
    high, medium, low = w["sev-high"], w["sev-medium"], w["sev-low"]
    passing, yellow, white = w["verdict-pass"], w["minfy-yellow"], w["surface"]

    out: list[tuple[str, str, str, str, str]] = []
    add = lambda section, desc, fg, bg, kind: out.append((section, desc, fg, bg, kind))

    s = "Findings accordion"
    add(s, "group heading, ink on surface", ink, surface, TEXT)
    add(s, "group count, ink-muted on surface", muted, surface, TEXT)
    add(s, "chevron stroke, ink-muted on surface", muted, surface, GRAPHIC)
    add(s, "chevron on hovered row, ink-muted on sunken", muted, sunken, GRAPHIC)
    add(s, "finding title on hovered row, ink on sunken", ink, sunken, TEXT)
    add(s, "passing finding title, ink-muted on sunken", muted, sunken, TEXT)
    add(s, "priority number, ink-faint on surface", faint, surface, TEXT)
    add(s, "priority number on hovered row, ink-faint on sunken", faint, sunken, TEXT)
    add(s, "check_id mono, ink-faint on surface", faint, surface, TEXT)
    add(s, "affected-component count, ink-muted on surface", muted, surface, TEXT)
    add(s, "status tag 'Not met', sev-high on sev-high/8", high, over(high, surface, 0.08), TEXT)
    add(s, "status tag 'Partial', sev-medium on sev-medium/8", medium, over(medium, surface, 0.08), TEXT)
    add(s, "status tag 'Met', verdict-pass on verdict-pass/8", passing, over(passing, surface, 0.08), TEXT)
    add(s, "status tag 'N/A', ink-faint on surface", faint, surface, TEXT)
    add(s, "remediation body, ink on sunken", ink, sunken, TEXT)
    # Decorative accent bar, not an information-bearing graphic: the "REMEDIATION"
    # eyebrow carries the meaning and the body text passes on its own. Listed so the
    # exemption is visible — raising the alpha from /40 to /70 would clear 3.0 if it
    # is ever wanted, but that is a component change, not a token one.
    add(s, "remediation rule, indigo/40 on sunken", over(indigo, sunken, 0.40), sunken, EXEMPT)
    add(s, "severity mark high, sev-high on surface", high, surface, GRAPHIC)
    add(s, "severity mark medium, sev-medium on surface", medium, surface, GRAPHIC)
    add(s, "severity mark low, sev-low on surface", low, surface, GRAPHIC)
    add(s, "severity label low, sev-low on surface", low, surface, TEXT)

    s = "Maturity tooltip"
    add(s, "info button glyph, ink-faint on surface", faint, surface, TEXT)
    add(s, "info button border, ink-faint on surface", faint, surface, GRAPHIC)
    add(s, "info button hovered, indigo on surface", indigo, surface, TEXT)
    add(s, "current tier, ink on surface", ink, surface, TEXT)
    add(s, "other tiers, ink-muted on surface", muted, surface, TEXT)
    add(s, "exclusive-bound note 11px, ink-faint on surface", faint, surface, TEXT)
    add(s, "panel border, hairline on surface", hairline, surface, EXEMPT)

    s = "Top action items"
    add(s, "item number, indigo on surface", indigo, surface, TEXT)
    add(s, "imperative line, ink on surface", ink, surface, TEXT)
    add(s, "context line, ink-muted on surface", muted, surface, TEXT)

    s = "Score badge"
    add(s, "score 48px, ink on surface", ink, surface, LARGE)
    add(s, "'/100' 22px, ink-muted on surface", muted, surface, LARGE)
    add(s, "'OVERALL · GOVERNED' 11px, ink-muted on surface", muted, surface, TEXT)

    s = "Pastel blocks"
    add(s, "executive summary, ink on cream", ink, cream, TEXT)
    add(s, "framework name, ink on sky", ink, sky, TEXT)
    add(s, "framework name, ink on mint", ink, mint, TEXT)
    add(s, "framework name, ink on teal", ink, teal, TEXT)
    add(s, "pillar meta, ink-muted on sky", muted, sky, TEXT)
    add(s, "pillar meta, ink-muted on mint", muted, mint, TEXT)
    add(s, "pillar meta, ink-muted on teal", muted, teal, TEXT)
    add(s, "'(n n/a)' count, ink-faint on sky", faint, sky, TEXT)
    add(s, "'(n n/a)' count, ink-faint on mint", faint, mint, TEXT)
    add(s, "pillar bar fill, indigo on ink/15 track over sky", indigo, over(ink, sky, 0.15), GRAPHIC)
    add(s, "pillar bar fill, verdict-pass on ink/15 over mint", passing, over(ink, mint, 0.15), GRAPHIC)
    add(s, "pillar bar track, ink/15 on sky", over(ink, sky, 0.15), sky, EXEMPT)
    add(s, "diagram-only note, ink-muted on sky", muted, sky, TEXT)
    add(s, "diagram-only note heading, ink on sky", ink, sky, TEXT)
    add(s, "history empty state, ink on tan", ink, tan, TEXT)
    add(s, "history empty state sub, ink-muted on tan", muted, tan, TEXT)

    s = "Header and nav"
    add(s, "wordmark, white on navy", white, navy, TEXT)
    add(s, "subtitle, white/60 on navy", over(white, navy, 0.60), navy, TEXT)
    add(s, "active tab, white on navy", white, navy, TEXT)
    add(s, "inactive tab, white/50 on navy", over(white, navy, 0.50), navy, TEXT)
    add(s, "logo mark block, white on navy", white, navy, GRAPHIC)
    add(s, "logo mark square, yellow on navy", yellow, navy, GRAPHIC)
    add(s, "footer fine print 12px, ink-faint on surface", faint, surface, TEXT)
    add(s, "step tracker active label, ink on sunken", ink, sunken, TEXT)
    add(s, "step tracker done label, ink-muted on sunken", muted, sunken, TEXT)
    add(s, "step tracker pending label, ink-faint on sunken", faint, sunken, TEXT)
    add(s, "step tracker numeral, white on navy", white, navy, GRAPHIC)

    s = "Controls"
    add(s, "primary button, white on indigo", white, indigo, TEXT)
    add(s, "primary button hovered, white on blue", white, blue, TEXT)
    add(s, "secondary button, navy on surface", navy, surface, TEXT)
    add(s, "disabled button, ink-faint on hairline", faint, hairline, EXEMPT)
    add(s, "text input placeholder, ink-faint on surface", faint, surface, TEXT)
    add(s, "focus ring, indigo on surface", indigo, surface, GRAPHIC)
    add(s, "token gate card, ink on surface over navy", ink, surface, TEXT)
    add(s, "copy fix-it prompt, navy on surface", navy, surface, TEXT)
    add(s, "copy fix-it prompt copied, white on navy", white, navy, TEXT)
    add(s, "copy failure message, sev-high on surface", high, surface, TEXT)

    # Three surfaces the audit did not reach until the visual pass over them. The gap
    # mattered: the version chips' only boundary was a hairline at 1.42:1 on the sunken
    # fill, well under the 3:1 WCAG 1.4.11 asks of a control's boundary, and nothing
    # here was measuring it.
    s = "AI detection panel"
    add(s, "verdict tag 'AI present', navy on navy/5 over sunken",
        navy, over(navy, sunken, 0.05), TEXT)
    add(s, "verdict tag 'AI likely', sev-medium on sev-medium/8 over sunken",
        medium, over(medium, sunken, 0.08), TEXT)
    add(s, "verdict tag 'No AI found', ink-faint on ink-faint/5 over sunken",
        faint, over(faint, sunken, 0.05), TEXT)
    # The tags' 1px borders. Decorative: each tag carries a tint AND a word, so the
    # border is not what identifies it. Listed so the exemption is visible rather than
    # assumed, matching how the remediation rule above is handled.
    add(s, "verdict tag border, navy/40 on navy/5 over sunken",
        over(navy, sunken, 0.40), over(navy, sunken, 0.05), EXEMPT)
    add(s, "verdict tag border, sev-medium/40 on sev-medium/8 over sunken",
        over(medium, sunken, 0.40), over(medium, sunken, 0.08), EXEMPT)
    # The legend for the count line. Prose, so it takes the body ink rather than the
    # mono line's faint tier; the three emphasised terms are full ink.
    add(s, "count legend, ink-muted on sunken", muted, sunken, TEXT)
    add(s, "count legend emphasis, ink on sunken", ink, sunken, TEXT)
    add(s, "evidence disclosure, indigo on sunken", indigo, sunken, TEXT)
    add(s, "evidence disclosure chevron, indigo on sunken", indigo, sunken, GRAPHIC)
    add(s, "signal name, ink on sunken", ink, sunken, TEXT)
    add(s, "signal tier, ink-faint on sunken", faint, sunken, TEXT)
    # The submitted text, quoted. It moved from ink-faint to ink-muted in the visual
    # pass: it was the faintest thing in the panel and it is the only part a reviewer
    # can check against the original document.
    add(s, "quoted excerpt mono, ink-muted on sunken", muted, sunken, TEXT)
    add(s, "signal source, ink-faint on sunken", faint, sunken, TEXT)
    add(s, "evidence item rule, ink/10 on sunken", over(ink, sunken, 0.10), sunken, EXEMPT)

    s = "Version banner and delta"
    add(s, "banner heading, ink on sunken", ink, sunken, TEXT)
    add(s, "'of N' count, ink-muted on sunken", muted, sunken, TEXT)
    add(s, "feedback eyebrow, ink-faint on sunken", faint, sunken, TEXT)
    add(s, "quoted feedback mono, ink-muted on sunken", muted, sunken, TEXT)
    add(s, "'Open the previous version', indigo on sunken", indigo, sunken, TEXT)
    # The regression the pass fixed. `border-hairline` here was the entire visual
    # boundary of a button, at 1.42:1.
    add(s, "version chip border, ink-faint on sunken", faint, sunken, GRAPHIC)
    add(s, "version chip label, ink on sunken", ink, sunken, TEXT)
    add(s, "version chip score, ink-muted on sunken", muted, sunken, TEXT)
    add(s, "current version chip, white on navy", white, navy, TEXT)
    add(s, "current version chip score, white/80 on navy",
        over(white, navy, 0.80), navy, TEXT)
    add(s, "delta stat value resolved, verdict-pass on sunken", passing, sunken, TEXT)
    add(s, "delta stat value new, sev-high on sunken", high, sunken, TEXT)
    add(s, "delta stat label, ink-muted on sunken", muted, sunken, TEXT)
    add(s, "delta pillar scores, ink-muted on sunken", muted, sunken, TEXT)

    s = "Feedback box"
    add(s, "heading, ink on sky", ink, sky, TEXT)
    add(s, "instruction, ink-muted on sky", muted, sky, TEXT)
    add(s, "textarea text, ink on surface", ink, surface, TEXT)
    # Was `hairline` — 1.38:1 against the sky fill outside the field and 1.52:1
    # against the white inside it. The only boundary of this panel's primary input.
    add(s, "textarea border, ink-faint on sky", faint, sky, GRAPHIC)
    add(s, "textarea border inner edge, ink-faint on surface", faint, surface, GRAPHIC)
    add(s, "dictation status, ink on sky", ink, sky, TEXT)
    add(s, "character counter, ink-muted on sky", muted, sky, TEXT)
    add(s, "mic glyph idle, white on indigo", white, indigo, GRAPHIC)
    add(s, "stop glyph listening, white on navy", white, navy, GRAPHIC)
    # The second, non-colour carrier of the listening state is the glyph swap; the ring
    # is the third. Measured anyway, since it is drawn on the sky fill.
    add(s, "listening ring, navy on sky", navy, sky, GRAPHIC)
    add(s, "mic tooltip, white on navy", white, navy, TEXT)
    add(s, "attachment disclosure, indigo on sky", indigo, sky, TEXT)
    add(s, "attachment note, ink-muted on sky", muted, sky, TEXT)
    add(s, "attachment rule, ink/10 on sky", over(ink, sky, 0.10), sky, EXEMPT)
    add(s, "submit button, white on indigo", white, indigo, TEXT)
    add(s, "blocker reason, ink-muted on sky", muted, sky, TEXT)
    add(s, "upload error, sev-high on sky", high, sky, TEXT)

    s = "PDF cover"
    add(s, "title 30pt, WHITE on NAVY", p["WHITE"], p["NAVY"], LARGE)
    add(s, "eyebrow 8.5pt, ACCENT_ON_DARK on NAVY", p["ACCENT_ON_DARK"], p["NAVY"], TEXT)
    add(s, "band 13pt bold, ACCENT_ON_DARK on NAVY", p["ACCENT_ON_DARK"], p["NAVY"], LARGE)
    add(s, "score 66pt, WHITE on NAVY", p["WHITE"], p["NAVY"], LARGE)
    add(s, "meta 10pt, COVER_META on NAVY", p["COVER_META"], p["NAVY"], TEXT)
    add(s, "footer 8pt, COVER_FOOT on NAVY", p["COVER_FOOT"], p["NAVY"], TEXT)
    add(s, "stat value 17pt, WHITE on NAVY", p["WHITE"], p["NAVY"], LARGE)
    add(s, "logo mark, WHITE on NAVY", p["WHITE"], p["NAVY"], GRAPHIC)
    add(s, "logo square, LOGO_YELLOW on NAVY", p["LOGO_YELLOW"], p["NAVY"], GRAPHIC)
    add(s, "top band, ACCENT on NAVY", p["ACCENT"], p["NAVY"], EXEMPT)
    add(s, "rule, ACCENT_LIGHT on NAVY", p["ACCENT_LIGHT"], p["NAVY"], EXEMPT)

    s = "PDF body"
    add(s, "h1/h2, NAVY on white", p["NAVY"], p["WHITE"], TEXT)
    add(s, "body 9.5pt, INK on white", p["INK"], p["WHITE"], TEXT)
    add(s, "muted 9pt, INK_MUTED on white", p["INK_MUTED"], p["WHITE"], TEXT)
    add(s, "small 8pt, INK_MUTED on white", p["INK_MUTED"], p["WHITE"], TEXT)
    add(s, "framework heading, NAVY on PASTEL_SKY", p["NAVY"], p["PASTEL_SKY"], TEXT)
    add(s, "framework heading, NAVY on PASTEL_MINT", p["NAVY"], p["PASTEL_MINT"], TEXT)
    add(s, "framework score, INK_MUTED on PASTEL_SKY", p["INK_MUTED"], p["PASTEL_SKY"], TEXT)
    add(s, "framework score, INK_MUTED on PASTEL_MINT", p["INK_MUTED"], p["PASTEL_MINT"], TEXT)
    add(s, "executive summary, INK on PASTEL_CREAM", p["INK"], p["PASTEL_CREAM"], TEXT)
    add(s, "finding rank, ACCENT on white", p["ACCENT"], p["WHITE"], TEXT)
    add(s, "remediation, INK on OFF_WHITE", p["INK"], p["OFF_WHITE"], TEXT)
    add(s, "remediation rule, ACCENT on OFF_WHITE", p["ACCENT"], p["OFF_WHITE"], GRAPHIC)
    add(s, "table rule, HAIRLINE on white", p["HAIRLINE"], p["WHITE"], EXEMPT)
    return out


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def audit() -> tuple[list[tuple[str, float, float]], list[tuple[str, float, float]]]:
    """Returns (failures, borderline). Borderline is passing with <15% margin."""
    failures, borderline = [], []
    for section, desc, fg, bg, kind in pairs():
        r = ratio(fg, bg)
        if kind == EXEMPT:
            continue
        need = THRESHOLD[kind]
        if r < need:
            failures.append((f"{section}: {desc}", r, need))
        elif r < need * 1.15:
            borderline.append((f"{section}: {desc}", r, need))
    return failures, borderline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-only", action="store_true")
    args = parser.parse_args()

    current = None
    for section, desc, fg, bg, kind in pairs():
        r = ratio(fg, bg)
        if kind == EXEMPT:
            verdict, need = "EXEMPT", 0.0
        else:
            need = THRESHOLD[kind]
            verdict = "PASS" if r >= need else "FAIL"
            if verdict == "PASS" and r < need * 1.15:
                verdict = "PASS*"
        if args.fail_only and verdict not in ("FAIL",):
            continue
        if section != current:
            print(f"\n{section}\n{'-' * 78}")
            current = section
        target = f"need {need:.1f}" if need else "decorative"
        print(f"  {verdict:<6} {r:6.2f}:1  {target:<12} {desc}")

    failures, borderline = audit()
    print(f"\n{'=' * 78}")
    print(f"{len(pairs())} pairs · {len(failures)} FAIL · {len(borderline)} borderline (<15% margin)")
    for label, r, need in failures:
        print(f"  FAIL       {r:6.2f}:1 (need {need}) {label}")
    for label, r, need in borderline:
        print(f"  BORDERLINE {r:6.2f}:1 (need {need}) {label}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
