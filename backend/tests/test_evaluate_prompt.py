"""What the evaluate stage is actually shown about the design.

## Why this file exists

An investigation into whether the human-in-the-loop check could see an explicit
A2I-style review loop turned on a question nothing in the suite answered: does the
DETERMINISTIC reading of the diagram reach the stage that scores it, or only the
classify model's re-narration of it?

The answer is that both do — `evaluate` passes `design.as_prompt_context()` and then
`_render_classification(classification)` — but nothing asserted it, and the two are
assembled at a call site far from either renderer. A refactor that dropped the first
block would leave every verdict resting on a model's paraphrase of a diagram the
parser had already read exactly, and no test would have noticed.

So this pins the property rather than the wording: for anything the parser resolved
deterministically — a component's service, a connection's label — the evaluate prompt
must contain it verbatim, whatever the classify stage did or did not say about it.

## What is deliberately NOT asserted

The exact prose of either block. These are prompts; they get reworded. The assertions
below are all "this fact survives", never "this sentence is present".
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from agent import stages, untrusted
from ingestion import drawio
from schema import NormalizedDesign

FIXTURE = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "fixtures" / "verification" / "a2i-human-review.drawio"
)


@pytest.fixture(scope="module")
def graph() -> Any:
    """The A2I fixture, through the real parser.

    A real diagram rather than a hand-built graph, because the point is what survives
    the whole path: `drawio.py` resolves `service=augmented ai` on the A2I box from its
    mxgraph shape style, and a hand-built fixture would assert the renderer while
    skipping the resolution that gives it something to render.
    """
    return drawio.parse(FIXTURE.read_bytes())


@pytest.fixture(scope="module")
def design(graph) -> NormalizedDesign:
    return NormalizedDesign(
        review_id="00000000-0000-4000-8000-000000000000",
        title="A2I", document_text="", graph=graph,
    )


def _evaluate_content(design: NormalizedDesign, classification: dict[str, Any]) -> str:
    """The user content `evaluate` builds, assembled the same way `evaluate` does.

    Duplicated from the call site rather than extracted from it, deliberately: an
    extracted helper that both use would make this test agree with `evaluate` by
    construction even after `evaluate` stopped passing one of the blocks.
    """
    return (
        f"{untrusted.wrap(design.as_prompt_context())}\n\n"
        f"## Component inventory (from the classification stage)\n"
        f"{untrusted.wrap(stages._render_classification(classification))}"
    )


def _forgetful_classification(graph) -> dict[str, Any]:
    """A classify output that has lost everything interesting about the design.

    The adversarial case, and the realistic one: classify is a sampled model call
    whose `data_flows` are free prose. If it summarises seven labelled edges as one
    vague sentence — as here — the deterministic block is the only place the actual
    labels still exist.
    """
    return {
        "design_summary": "A claims pipeline.",
        "components": [
            {"id": c.id, "label": c.label, "kind": "unknown", "provider": "unknown",
             "service": "", "attributes": []}
            for c in graph.components
        ],
        "data_flows": [
            {"description": "Data moves between the components.",
             "crosses_trust_boundary": False, "carries_sensitive_data": False}
        ],
        "observations": [],
        "absent": [],
    }


# --------------------------------------------------------------------------- #
# The deterministic reading reaches evaluate
# --------------------------------------------------------------------------- #

def test_the_parsed_connection_labels_reach_evaluate_verbatim(design, graph) -> None:
    """The property the investigation was about.

    Every edge the parser read, with its label, must be in the prompt — including the
    two that carry the human-review mechanism: the reviewer's return edge, and the
    store's gate. A classify stage that never mentions them cannot hide them.
    """
    content = _evaluate_content(design, _forgetful_classification(graph))

    assert graph.connections, "the fixture has no edges; this would prove nothing"
    for edge in graph.connections:
        assert f"{edge.source_id} -> {edge.target_id} ({edge.label})" in content, edge

    # Named explicitly, because these two are the whole reason the file exists.
    assert "reviewer -> a2i (reviewer approves or overrides the decision)" in content
    assert "a2i -> store (human-approved outcome only)" in content


def test_a_deterministically_resolved_service_reaches_evaluate(design, graph) -> None:
    """`augmented ai` is what identifies the A2I box as a human-review service.

    Its `kind` is `unknown` — the keyword map has no entry for a review loop — so the
    resolved service is the only machine-readable signal on that component. If it
    stops reaching the prompt, the box is an unlabelled rectangle to the evaluator.
    """
    content = _evaluate_content(design, _forgetful_classification(graph))

    a2i = next(c for c in graph.components if c.id == "a2i")
    assert a2i.service == "augmented ai", "the parser stopped resolving this"
    assert "service=augmented ai" in content


def test_the_classification_block_does_not_replace_the_deterministic_one(
    design, graph
) -> None:
    """Both, not either. The mutation this catches is a refactor that renders only the
    classification — which reads like a simplification and silently makes every verdict
    rest on a paraphrase."""
    content = _evaluate_content(design, _forgetful_classification(graph))

    assert "## Components (from the architecture diagram)" in content
    assert "## Component inventory (from the classification stage)" in content
    # And the deterministic block comes first, so it frames the paraphrase rather
    # than trailing it as a footnote.
    assert content.index("from the architecture diagram") < content.index(
        "from the classification stage"
    )


def test_evaluate_itself_passes_both_blocks() -> None:
    """Asserted against `evaluate`'s own source, since the helper above only proves
    the two renderers work — not that the stage still calls both."""
    import inspect

    source = inspect.getsource(stages.evaluate)

    assert "design.as_prompt_context()" in source
    assert "_render_classification(classification)" in source


# --------------------------------------------------------------------------- #
# The `service` field the classification block used to drop
# --------------------------------------------------------------------------- #

def test_the_classification_block_carries_service_when_it_has_one() -> None:
    """It did not, and the result was two descriptions of one component that
    disagreed — the deterministic one naming a service, the one below it stopping at
    `provider=aws`."""
    rendered = stages._render_classification({
        "design_summary": "s",
        "components": [{"id": "a2i", "label": "A2I Review Loop", "kind": "unknown",
                        "provider": "aws", "service": "augmented ai",
                        "attributes": []}],
        "data_flows": [], "observations": [], "absent": [],
    })

    assert "service=augmented ai" in rendered


def test_an_unresolved_service_is_omitted_rather_than_rendered_empty() -> None:
    """`service=` with nothing after it reads as "no service", which is a claim. Not
    identifying one is a different statement from there not being one."""
    for value in ("", "   ", None):
        rendered = stages._render_classification({
            "design_summary": "s",
            "components": [{"id": "x", "label": "Box", "kind": "unknown",
                            "provider": "unknown", "service": value,
                            "attributes": []}],
            "data_flows": [], "observations": [], "absent": [],
        })
        assert "service=" not in rendered, repr(value)


def test_attributes_still_render_after_the_service(design) -> None:
    """The two are adjacent in the line; adding one must not swallow the other."""
    rendered = stages._render_classification({
        "design_summary": "s",
        "components": [{"id": "db", "label": "Orders", "kind": "database",
                        "provider": "aws", "service": "dynamodb",
                        "attributes": [{"name": "encryption", "value": "none stated"}]}],
        "data_flows": [], "observations": [], "absent": [],
    })

    assert "service=dynamodb (encryption=none stated)" in rendered


# --------------------------------------------------------------------------- #
# The fixture itself
# --------------------------------------------------------------------------- #

def test_the_a2i_fixture_still_describes_a_human_review_loop(graph) -> None:
    """It is the input to a paid probe run. If it silently stops containing the
    pattern, that run measures nothing and costs the same."""
    labels = {c.label for c in graph.components}
    assert any("A2I" in label for label in labels)
    assert any("Reviewer" in label for label in labels)

    edges = {(e.source_id, e.target_id) for e in graph.connections}
    # Bidirectional: the loop assigns work to a human AND takes a decision back.
    assert ("a2i", "reviewer") in edges
    assert ("reviewer", "a2i") in edges
    # And the store is reachable from the review loop, so approval gates the write.
    assert ("a2i", "store") in edges
