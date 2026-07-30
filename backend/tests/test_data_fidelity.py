"""Three fidelity numbers, and the rule that they are never blended.

Each measures a different thing against a different kind of reference, and the
tests are organised around what would go wrong if that distinction were lost:

* structural coverage is EXACT, so it must read 100% on a cleanly-parsed diagram.
  A metric that reports 85% on a perfect file would fire the review threshold on
  every upload and be switched off within a week.
* the OCR proxy is an ESTIMATE, so it must be absent rather than zero when it
  cannot run, and must carry `is_estimate` on every path.
* the grounding filter is a COUNT, so it must never grow a percentage — "3 of 5
  removed" presented as "60% grounded" would invert what it means.

The last section asserts the absence of a composite. That is the failure this
whole design is arranged to prevent, and absence is not something a reader of the
code would notice, so it is tested.
"""

from __future__ import annotations

import importlib
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient

import config
import llm
import rubric
import schema
from ingestion import drawio, fidelity
from schema import (
    Component,
    Connection,
    DataFidelity,
    DesignGraph,
    GroundingFilter,
    OcrCoverageProxy,
    StructuralCoverage,
)

DEMO_TOKEN = "fidelity-token"


def _drawio(cells: str) -> bytes:
    """A draw.io file with the two mandatory root/layer cells and `cells` inside."""
    return (
        "<mxfile><diagram><mxGraphModel><root>"
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}</root></mxGraphModel></diagram></mxfile>"
    ).encode()


def _labelled(count: int, prefix: str = "c") -> str:
    return "".join(
        f'<mxCell id="{prefix}{i}" value="Service {i}" vertex="1" parent="1"/>'
        for i in range(count)
    )


# --------------------------------------------------------------------------- #
# 1. Structural coverage — exact, deterministic, no model call
# --------------------------------------------------------------------------- #

def test_a_cleanly_parsed_diagram_reads_100_percent() -> None:
    """The load-bearing case.

    An exact metric that cannot reach 100% on a perfect file is worse than none: it
    would trip the review threshold on every upload, and a warning that always fires
    is a warning nobody reads.
    """
    raw = _drawio(
        _labelled(4)
        + '<mxCell id="e0" edge="1" source="c0" target="c1" parent="1"/>'
        + '<mxCell id="e1" edge="1" source="c1" target="c2" parent="1"/>'
    )

    coverage = fidelity.structural_coverage(drawio.parse(raw), raw)

    assert coverage is not None
    assert coverage.percent == 100.0
    assert coverage.parsed_elements == coverage.total_elements == 6
    assert coverage.dropped == []


def test_the_root_and_layer_cells_are_excluded_from_the_denominator() -> None:
    """Every mxGraphModel contains `id="0"` and `id="1"`. They are container
    scaffolding, carry neither vertex nor edge, and are never diagram content.

    Counting them is the specific mistake that caps a perfect 11-element diagram at
    11/13 = 84.6%, so it is pinned directly rather than left to the test above.
    """
    raw = _drawio(_labelled(4))

    coverage = fidelity.structural_coverage(drawio.parse(raw), raw)

    assert coverage is not None
    assert coverage.total_elements == 4, "the root and layer cells leaked into the count"


def test_the_shipped_drawio_fixtures_both_read_100_percent() -> None:
    """Against real files rather than constructed ones, so the metric is calibrated
    on the kind of diagram it will actually meet."""
    import pathlib

    ground_truth = pathlib.Path(__file__).resolve().parent.parent.parent / "fixtures" / "ground_truth"
    for name in ("expense-portal.drawio", "claims-triage-ai.drawio"):
        raw = (ground_truth / name).read_bytes()
        coverage = fidelity.structural_coverage(drawio.parse(raw), raw)
        assert coverage is not None and coverage.percent == 100.0, (name, coverage)


def test_duplicate_ids_drop_coverage_and_are_named() -> None:
    """What a badly merged or copy-pasted export produces. The parser keeps the
    first cell with an id and drops the rest."""
    raw = _drawio(
        "".join(f'<mxCell id="dup" value="Svc {i}" vertex="1" parent="1"/>' for i in range(10))
        + '<mxCell id="u" value="API" vertex="1" parent="1"/>'
    )

    coverage = fidelity.structural_coverage(drawio.parse(raw), raw)

    assert coverage is not None
    assert coverage.percent < schema.COVERAGE_REVIEW_THRESHOLD
    assert any("sharing an id" in reason for reason in coverage.dropped)
    assert any(reason.startswith("9 ") for reason in coverage.dropped)


def test_unlabelled_shapes_drop_coverage_and_say_that_this_is_correct() -> None:
    """`drawio.parse` drops unlabelled shapes on purpose — decoration, containers,
    connectors with no text. The percentage falls, so the REASON has to explain that
    it is correct behaviour, or an accurate number reads as a bug."""
    raw = _drawio(
        _labelled(6) + "".join(f'<mxCell id="d{i}" vertex="1" parent="1"/>' for i in range(6))
    )

    coverage = fidelity.structural_coverage(drawio.parse(raw), raw)

    assert coverage is not None
    assert coverage.percent == 50.0
    assert coverage.dropped == ["6 unlabelled shapes, which carry no reviewable meaning"]


def test_dangling_edges_drop_coverage_and_are_named() -> None:
    raw = _drawio(
        _labelled(4)
        + "".join(
            f'<mxCell id="e{i}" edge="1" source="ghost{i}" target="c0" parent="1"/>'
            for i in range(6)
        )
    )

    coverage = fidelity.structural_coverage(drawio.parse(raw), raw)

    assert coverage is not None
    assert any("endpoints are not components" in reason for reason in coverage.dropped)


def test_an_unrecognised_construct_is_reported_as_unaccounted() -> None:
    """The remainder is surfaced rather than absorbed.

    Each reason is derived independently from the XML, so they can fail to add up.
    A breakdown that silently swallowed the difference would make a parser gap look
    like nothing at all.
    """
    coverage = StructuralCoverage(
        parsed_elements=2, total_elements=10, percent=20.0,
        dropped=fidelity._dropped_reasons(
            DesignGraph(), "<mxGraphModel/>", [], [], parsed=2, total=10
        ),
    )

    assert any("unaccounted" in reason for reason in coverage.dropped)


def test_coverage_can_never_exceed_100_percent() -> None:
    """The two sides are counted by different code — a regex here, a real XML parse
    in drawio.py — so they can disagree at the margins. A figure above 100% would
    destroy trust in the metric far more than the clamp hides."""
    raw = _drawio(_labelled(2))
    inflated = DesignGraph(
        components=[Component(id=f"c{i}", label="x") for i in range(50)],
        connections=[Connection(source_id="c0", target_id="c1")],
        notes=["a", "b"],
    )

    coverage = fidelity.structural_coverage(inflated, raw)

    assert coverage is not None and coverage.percent == 100.0


def test_a_compressed_export_is_not_measured_rather_than_guessed() -> None:
    """Counting it needs the payload inflated a second time, and a wrong denominator
    is worse than no metric."""
    assert fidelity.structural_coverage(
        DesignGraph(), b"<mxfile><diagram>7VvbcuI4EP0aP-4Ujg==</diagram></mxfile>"
    ) is None


def test_structural_coverage_needs_no_model_call(monkeypatch) -> None:
    """Deterministic, asserted by making any model call explode."""
    def explode(**kwargs: Any):
        raise AssertionError("structural coverage must not call a model")

    monkeypatch.setattr(llm, "complete_json", explode)
    raw = _drawio(_labelled(3))

    assert fidelity.structural_coverage(drawio.parse(raw), raw).percent == 100.0


# --------------------------------------------------------------------------- #
# 2. OCR coverage proxy — an estimate, and labelled as one on every path
# --------------------------------------------------------------------------- #

def _png(text_lines: list[str], size: tuple[int, int] = (900, 400)) -> bytes:
    """A real PNG with real rendered text, so OCR has something genuine to read."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(text_lines):
        draw.text((20, 20 + index * 40), line, fill=(0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


requires_ocr = pytest.mark.skipif(
    not fidelity.ocr_available()[0],
    reason="no OCR engine installed; the proxy reports itself unavailable instead",
)


def test_is_estimate_is_true_on_every_path() -> None:
    """It exists so the UI cannot present this as a measurement by omission, so it
    must hold even on the unavailable and failed paths."""
    assert OcrCoverageProxy(available=False).is_estimate is True
    assert OcrCoverageProxy(available=True, ocr_tokens=1, matched_tokens=1).is_estimate is True


def test_an_absent_engine_makes_the_metric_absent_not_zero(monkeypatch) -> None:
    """A 0% would read as "the vision model missed everything" — a claim about the
    model, when the truth is a claim about our tooling."""
    monkeypatch.setattr(fidelity, "ocr_available", lambda: (False, "no engine here"))

    proxy = fidelity.ocr_coverage_proxy(DesignGraph(), b"")

    assert proxy.available is False
    assert proxy.percent == 0.0
    assert proxy.unavailable_reason == "no engine here"
    # And `available` is what the UI branches on, so the zero is never shown.
    assert DataFidelity(ocr_proxy=proxy).review_recommended() is False, (
        "an unavailable estimate must not trigger the review recommendation"
    )


def test_a_failed_ocr_pass_is_absorbed_rather_than_breaking_the_review(monkeypatch) -> None:
    """This is a diagnostic on a review that already succeeded. It must never be the
    thing that fails one."""
    monkeypatch.setattr(fidelity, "ocr_available", lambda: (True, ""))

    proxy = fidelity.ocr_coverage_proxy(DesignGraph(), b"not an image at all")

    assert proxy.available is False
    assert "OCR pass failed" in proxy.unavailable_reason


@requires_ocr
def test_a_graph_containing_the_image_text_scores_high() -> None:
    """A real OCR pass against a real PNG. Nothing stubbed."""
    image = _png(["CloudFront", "DynamoDB", "ApiGateway"])
    graph = DesignGraph(
        components=[
            Component(id="cdn", label="CloudFront", kind="cdn", provider="aws"),
            Component(id="db", label="DynamoDB", kind="database", provider="aws"),
            Component(id="api", label="ApiGateway", kind="gateway", provider="aws"),
        ]
    )

    proxy = fidelity.ocr_coverage_proxy(graph, image)

    assert proxy.available is True
    assert proxy.percent >= 60.0, (proxy.percent, proxy.sample_unmatched)
    assert proxy.ocr_tokens > 0


@requires_ocr
def test_an_empty_graph_against_a_text_heavy_image_scores_low_and_recommends_review() -> None:
    image = _png(["CloudFront", "DynamoDB", "ApiGateway", "SecretsManager"])

    proxy = fidelity.ocr_coverage_proxy(DesignGraph(), image)

    assert proxy.available is True
    assert proxy.percent < schema.COVERAGE_REVIEW_THRESHOLD
    assert proxy.sample_unmatched, "the unmatched sample is how a reviewer judges this"
    assert DataFidelity(ocr_proxy=proxy).review_recommended() is True


@requires_ocr
def test_an_image_ocr_cannot_read_is_unavailable_rather_than_zero_percent() -> None:
    """A diagram carrying its meaning in shapes rather than words is not 0% covered
    — it is not measurable by this method at all."""
    from PIL import Image

    blank = Image.new("RGB", (400, 300), (255, 255, 255))
    buffer = io.BytesIO()
    blank.save(buffer, format="PNG")

    proxy = fidelity.ocr_coverage_proxy(DesignGraph(), buffer.getvalue())

    assert proxy.available is False
    assert "not a coverage figure of zero" in proxy.unavailable_reason


def test_the_ratio_is_over_distinct_words_not_word_instances() -> None:
    """A legend repeating "AWS" nine times must not move the score more than nine
    genuinely distinct missed components."""
    assert fidelity._tokens("aws aws aws gateway") == {"aws", "gateway"}


def test_short_tokens_and_stopwords_are_dropped_from_both_sides() -> None:
    """OCR produces a lot of one- and two-character noise from arrowheads and
    borders; matching on it would swamp the signal in both directions."""
    tokens = fidelity._tokens("a to the gateway and x1 database")

    assert tokens == {"gateway", "database"}


def test_graph_text_includes_every_field_a_transcribed_word_could_land_in() -> None:
    """A word the model filed as a protocol or a note WAS extracted. Counting it as
    missed would understate coverage."""
    graph = DesignGraph(
        components=[
            Component(id="alb", label="Balancer", kind="load_balancer",
                      provider="aws", service="application load balancer",
                      attributes={"encryption": "kms"})
        ],
        connections=[Connection(source_id="alb", target_id="alb", label="Ingress",
                                protocol="https")],
        notes=["Region apsouth1"],
    )

    text = fidelity.graph_text(graph).lower()

    for word in ("balancer", "kms", "ingress", "https", "apsouth1", "application"):
        assert word in text, word


# --------------------------------------------------------------------------- #
# 3. Grounding filter — a count, never a rate
# --------------------------------------------------------------------------- #

def test_the_grounding_filter_model_has_no_percentage_field() -> None:
    """"3 of 5 removed" shown as "60% grounded" would invert the meaning: a claim
    whose quote was verifiable is not thereby correct."""
    fields = set(GroundingFilter.model_fields)

    assert not {f for f in fields if "percent" in f or "rate" in f}, fields


def test_removed_and_incomplete_are_counted_separately() -> None:
    """One is the model making a claim it cannot support, the other is the model
    returning nonsense. Folding them together would overstate how much ungrounded
    assertion the filter is catching."""
    from agent import stages

    payload = {
        "use_case_notes": [
            # Grounded: the quote is in the context.
            {"component": "cache", "recommendation": "add one",
             "grounded_in": "read-heavy access pattern"},
            # Ungrounded: a quote that was never written.
            {"component": "queue", "recommendation": "add one",
             "grounded_in": "we need sub-millisecond latency"},
            # Incomplete: no recommendation at all.
            {"component": "db", "recommendation": "", "grounded_in": "read-heavy"},
        ]
    }

    notes, grounding = stages._use_case_notes(
        payload, "The workload has a read-heavy access pattern."
    )

    assert len(notes) == 1
    assert grounding is not None
    assert grounding.checked == 3
    assert grounding.removed == 1
    assert grounding.incomplete == 1
    assert grounding.removed_for == ["queue"]


def test_no_context_reports_no_filter_rather_than_zero_caught() -> None:
    """A filter that could not run caught nothing. "0 caught" would imply it looked."""
    from agent import stages

    notes, grounding = stages._use_case_notes(
        {"use_case_notes": [{"component": "c", "recommendation": "r", "grounded_in": "q"}]},
        "",
    )

    assert notes == []
    assert grounding is None


def test_a_run_with_no_open_findings_reports_no_filter(monkeypatch) -> None:
    """No model call was made, so the filter never ran."""
    from agent import stages

    passing = [
        __import__("schema").Finding(
            framework=c.framework, pillar_id=c.pillar_id, check_id=c.check_id,
            status="pass", severity=c.severity, title=c.description,
        )
        for c in rubric.all_checks()
    ]

    *_, grounding = stages.remediate(passing, {}, "sb", context="anything")

    assert grounding is None


# --------------------------------------------------------------------------- #
# The rule: three numbers, never one
# --------------------------------------------------------------------------- #

def test_data_fidelity_has_no_composite_field() -> None:
    """The failure this whole design is arranged to prevent.

    A single "accuracy %" over an exact ratio, an estimate, and a count is arithmetic
    on incompatible quantities, and it would launder the estimate's uncertainty and
    the count's silence into a figure that looks measured. Absence is not something a
    reader would notice, so it is asserted.
    """
    fields = set(DataFidelity.model_fields)

    assert fields == {"structural", "ocr_proxy", "grounding"}, fields
    for banned in ("overall", "composite", "accuracy", "score", "combined", "total"):
        assert not [f for f in fields if banned in f], (banned, fields)


def test_the_grounding_count_never_triggers_the_review_recommendation() -> None:
    """Removing an ungrounded claim is the filter working, not a reason to distrust
    the review. Feeding it into the recommendation would punish a run for being
    filtered well."""
    fidelity_with_many_removed = DataFidelity(
        grounding=GroundingFilter(checked=20, removed=19, removed_for=["a"] * 19)
    )

    assert fidelity_with_many_removed.review_recommended() is False


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(100.0, False), (95.0, False), (94.9, True), (50.0, True)],
)
def test_the_review_threshold_is_applied_to_structural_coverage(percent, expected) -> None:
    measured = DataFidelity(
        structural=StructuralCoverage(
            parsed_elements=1, total_elements=1, percent=percent
        )
    )

    assert measured.review_recommended() is expected


def test_review_recommended_is_computed_rather_than_stored() -> None:
    """So it cannot drift from the numbers it describes."""
    assert "review_recommended" not in DataFidelity.model_fields
    assert callable(DataFidelity.review_recommended)


# --------------------------------------------------------------------------- #
# End to end, through the real routes
# --------------------------------------------------------------------------- #

def _stub():
    def fake(*, system, content, schema, effort, max_tokens, label="", temperature=None):
        required = set(schema.get("required", []))
        if "verdict" in required:
            return {"verdict": "reviewable", "subject": "a design",
                    "reason": "r", "confidence": "high"}, {}
        if "design_summary" in required:
            return {"design_summary": "x", "components": [], "data_flows": [],
                    "observations": [], "absent": []}, {}
        if "findings" in required:
            return {"findings": [
                {"check_id": c.check_id, "status": "fail", "severity": c.severity,
                 "severity_rationale": "s", "title": c.description, "evidence": "e",
                 "affected_components": []}
                for c in rubric.all_checks()
            ]}, {}
        if "ranking" in required:
            return {"summary": "- s", "ranking": []}, {}
        return {
            "executive_summary": "s",
            "remediations": [],
            # Two ungrounded claims and one grounded one, so the filter has
            # something real to catch on the way through the pipeline.
            "use_case_notes": [
                {"component": "cache", "recommendation": "add one",
                 "grounded_in": "read-heavy access pattern"},
                {"component": "queue", "recommendation": "add one",
                 "grounded_in": "a phrase never submitted"},
                {"component": "db", "recommendation": "shard it",
                 "grounded_in": "another phrase never submitted"},
            ],
        }, {}
    return fake


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import main
    import storage

    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    importlib.reload(storage)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    monkeypatch.setattr(llm, "complete_json", _stub())
    return TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: DEMO_TOKEN})


def _review(client: TestClient, name: str, data: bytes, **body: Any) -> dict:
    key = client.post(
        "/uploads", files={"file": (name, data, "application/octet-stream")}
    ).json()["key"]
    field = "diagram_key" if name.endswith((".drawio", ".png")) else "document_key"
    accepted = client.post("/reviews", json={field: key, **body})
    assert accepted.status_code == 202, accepted.json()
    return client.get(f"/reviews/{accepted.json()['review_id']}").json()


def test_a_drawio_review_carries_structural_coverage_and_no_ocr_proxy(client) -> None:
    result = _review(client, "design.drawio", _drawio(_labelled(5)))

    measured = result["fidelity"]
    assert measured["structural"]["percent"] == 100.0
    assert measured["structural"]["total_elements"] == 5
    # There is nothing to OCR on this path, and a structural ratio is not even
    # definable on the other. Neither is faked as zero.
    assert measured["ocr_proxy"] is None


def test_a_low_coverage_drawio_review_surfaces_the_percentage_and_the_reasons(
    client,
) -> None:
    raw = _drawio(
        "".join(f'<mxCell id="dup" value="Svc {i}" vertex="1" parent="1"/>' for i in range(12))
    )

    measured = _review(client, "merged.drawio", raw)["fidelity"]

    assert measured["structural"]["percent"] < schema.COVERAGE_REVIEW_THRESHOLD
    assert measured["structural"]["dropped"]


def test_the_grounding_count_reaches_the_stored_result(client) -> None:
    """Two of the three notes the stub returns quote phrases never submitted."""
    result = _review(
        client, "sow.md", b"# Design\n\nThe workload has a read-heavy access pattern.\n",
        context="The workload has a read-heavy access pattern.",
    )

    grounding = result["fidelity"]["grounding"]
    assert grounding["checked"] == 3
    assert grounding["removed"] == 2
    assert sorted(grounding["removed_for"]) == ["db", "queue"]
    # And the surviving note is the grounded one.
    assert [n["component"] for n in result["use_case_notes"]] == ["cache"]


def test_a_document_only_review_measures_neither_coverage_metric(client) -> None:
    """Both are diagram metrics. A document upload has no diagram to measure."""
    measured = _review(client, "sow.md", b"# Design\n\nProse about a system.\n")["fidelity"]

    assert measured["structural"] is None
    assert measured["ocr_proxy"] is None


def test_an_older_stored_review_without_fidelity_still_loads() -> None:
    """Reviews written before this existed must not fail validation, and must read as
    "not measured" rather than as zero coverage."""
    result = schema.ReviewResult(review_id="old", created_at="2026-01-01T00:00:00Z")

    assert result.fidelity.structural is None
    assert result.fidelity.ocr_proxy is None
    assert result.fidelity.grounding is None
    assert result.fidelity.review_recommended() is False
