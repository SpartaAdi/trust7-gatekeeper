"""The draw.io path is deterministic, so it is testable without any model call."""

from __future__ import annotations

import base64
import urllib.parse
import zlib

import pytest

from ingestion import drawio
from schema import DiagramSource

PLAIN = """<mxfile host="app.diagrams.net">
  <diagram name="Page-1">
    <mxGraphModel dx="1" dy="1" grid="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="vpc" value="Production VPC" style="shape=mxgraph.aws4.group;container=1"
                vertex="1" parent="1"/>
        <mxCell id="api" value="API Gateway"
                style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.api_gateway"
                vertex="1" parent="1"/>
        <mxCell id="fn" value="Review Lambda"
                style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda"
                vertex="1" parent="1"/>
        <mxCell id="db" value="Reviews&lt;br&gt;DynamoDB"
                style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.dynamodb"
                vertex="1" parent="1"/>
        <mxCell id="note" value="Assumes SSO is already in place" style="text;html=1"
                vertex="1" parent="1"/>
        <mxCell id="unlabelled" value="" style="rounded=0" vertex="1" parent="1"/>
        <mxCell id="e1" value="HTTPS" style="edgeStyle=none" edge="1" parent="1"
                source="api" target="fn"/>
        <mxCell id="e2" value="" edge="1" parent="1" source="fn" target="db"/>
        <mxCell id="e3" value="dangling" edge="1" parent="1" source="fn" target="unlabelled"/>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


def _compress(xml: str) -> bytes:
    """Reproduce draw.io's compressed-diagram encoding."""
    inner = xml[xml.index("<mxGraphModel") : xml.index("</mxGraphModel>") + len("</mxGraphModel>")]
    quoted = urllib.parse.quote(inner, safe="")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    raw = compressor.compress(quoted.encode()) + compressor.flush()
    payload = base64.b64encode(raw).decode()
    return f'<mxfile><diagram name="Page-1">{payload}</diagram></mxfile>'.encode()


def test_parses_plain_xml() -> None:
    graph = drawio.parse(PLAIN.encode())

    assert graph.source is DiagramSource.DRAWIO
    labels = {c.label for c in graph.components}
    assert {"API Gateway", "Review Lambda", "Reviews DynamoDB", "Production VPC"} <= labels


def test_classifies_kind_provider_and_service() -> None:
    graph = drawio.parse(PLAIN.encode())
    by_id = {c.id: c for c in graph.components}

    assert by_id["api"].kind == "gateway"
    assert by_id["fn"].kind == "compute"
    assert by_id["db"].kind == "database"
    assert by_id["vpc"].kind == "network_boundary"
    assert by_id["api"].provider == "aws"
    assert by_id["db"].service == "dynamodb"


def test_html_in_labels_is_stripped() -> None:
    graph = drawio.parse(PLAIN.encode())
    assert any(c.label == "Reviews DynamoDB" for c in graph.components)


def test_text_shapes_become_notes_not_components() -> None:
    graph = drawio.parse(PLAIN.encode())

    assert "Assumes SSO is already in place" in graph.notes
    assert all("Assumes SSO" not in c.label for c in graph.components)


def test_unlabelled_shapes_are_skipped() -> None:
    graph = drawio.parse(PLAIN.encode())
    assert "unlabelled" not in {c.id for c in graph.components}


def test_edges_are_kept_only_between_kept_components() -> None:
    graph = drawio.parse(PLAIN.encode())
    pairs = {(e.source_id, e.target_id) for e in graph.connections}

    assert ("api", "fn") in pairs
    assert ("fn", "db") in pairs
    # The edge into the dropped unlabelled shape must not survive.
    assert ("fn", "unlabelled") not in pairs


def test_protocol_is_read_from_the_edge_label() -> None:
    graph = drawio.parse(PLAIN.encode())
    edge = next(e for e in graph.connections if (e.source_id, e.target_id) == ("api", "fn"))
    assert edge.protocol == "https"


def test_compressed_and_plain_diagrams_agree() -> None:
    """The compressed path must produce the identical common-schema graph."""
    plain = drawio.parse(PLAIN.encode())
    compressed = drawio.parse(_compress(PLAIN))

    assert [c.model_dump() for c in compressed.components] == [
        c.model_dump() for c in plain.components
    ]
    assert [e.model_dump() for e in compressed.connections] == [
        e.model_dump() for e in plain.connections
    ]


def test_object_wrapper_labels_are_read() -> None:
    xml = """<mxfile><diagram><mxGraphModel><root>
      <mxCell id="0"/>
      <object label="Customer PII Store" id="store">
        <mxCell style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.s3"
                vertex="1" parent="0"/>
      </object>
    </root></mxGraphModel></diagram></mxfile>"""

    graph = drawio.parse(xml.encode())
    component = next(c for c in graph.components if c.id == "store")
    assert component.label == "Customer PII Store"
    assert component.kind == "storage"


def test_non_xml_input_raises() -> None:
    with pytest.raises(drawio.DrawioParseError):
        drawio.parse(b"this is not a diagram")


def test_xml_without_a_graph_model_raises() -> None:
    with pytest.raises(drawio.DrawioParseError):
        drawio.parse(b"<mxfile><diagram></diagram></mxfile>")
