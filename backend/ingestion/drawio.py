"""Deterministic draw.io XML parsing — no LLM call on this path.

draw.io stores a diagram either as plain `<mxGraphModel>` XML or as a
deflate-compressed, URL-encoded, base64 blob inside `<diagram>`. Both are
handled here, and both produce the same `DesignGraph` the vision path produces.
"""

from __future__ import annotations

import base64
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib

from schema import Component, Connection, DesignGraph, DiagramSource

# Style/label keywords mapped to the common schema's component kinds. Generic to
# any design — these are architectural categories, not a service catalogue.
_KIND_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("lambda", "compute"),
    ("function", "compute"),
    ("ec2", "compute"),
    ("container", "compute"),
    ("ecs", "compute"),
    ("eks", "compute"),
    ("kubernetes", "compute"),
    ("server", "compute"),
    ("s3", "storage"),
    ("bucket", "storage"),
    ("blob", "storage"),
    ("efs", "storage"),
    ("dynamo", "database"),
    ("rds", "database"),
    ("aurora", "database"),
    ("postgres", "database"),
    ("mysql", "database"),
    ("mongo", "database"),
    ("database", "database"),
    ("redshift", "analytics"),
    ("athena", "analytics"),
    ("glue", "analytics"),
    ("kinesis", "streaming"),
    ("kafka", "streaming"),
    ("sqs", "queue"),
    ("queue", "queue"),
    ("sns", "messaging"),
    ("eventbridge", "messaging"),
    ("apigateway", "gateway"),
    ("api gateway", "gateway"),
    ("alb", "load_balancer"),
    ("elb", "load_balancer"),
    ("load balancer", "load_balancer"),
    ("cloudfront", "cdn"),
    ("cdn", "cdn"),
    ("waf", "security_control"),
    ("shield", "security_control"),
    ("kms", "security_control"),
    ("secret", "security_control"),
    ("guardduty", "security_control"),
    ("iam", "identity"),
    ("cognito", "identity"),
    ("okta", "identity"),
    ("entra", "identity"),
    ("active directory", "identity"),
    ("vpc", "network_boundary"),
    ("subnet", "network_boundary"),
    ("nat", "network_boundary"),
    ("route53", "dns"),
    ("dns", "dns"),
    ("cloudwatch", "observability"),
    ("prometheus", "observability"),
    ("grafana", "observability"),
    ("datadog", "observability"),
    ("splunk", "observability"),
    # AI/ML. Note what is deliberately NOT here: a bare `"model"`.
    #
    # It was, and matching is plain substring, so every label containing the word
    # became `ai_model`. Measured against 25 labels: it caught 5 of 5 explicitly
    # named AI services and 0 of 15 implicit ones, while misclassifying "Domain
    # Model", "Cost Model", "Provisioning Model", "Threat Model Doc" and "Data Model
    # Registry" — 5 false positives out of 5 non-AI labels carrying the word.
    #
    # That mattered beyond the inventory: `kind` is rendered into the evaluate
    # stage's component block, so a design whose only "model" was a cost model
    # arrived at the AI-governance checks looking like it had a model in it.
    #
    # So the word only appears inside phrases that fix its sense. Anything genuinely
    # ambiguous is left as `unknown`, which is honest, and the AI evidence record in
    # `agent/ai_detection.py` catches the implicit cases this map cannot.
    ("bedrock", "ai_model"),
    ("sagemaker", "ai_model"),
    ("claude", "ai_model"),
    ("openai", "ai_model"),
    ("anthropic", "ai_model"),
    ("vertex ai", "ai_model"),
    ("llm", "ai_model"),
    ("machine learning", "ai_model"),
    ("ml model", "ai_model"),
    ("foundation model", "ai_model"),
    ("model registry", "ai_model"),
    ("model serving", "ai_model"),
    ("model endpoint", "ai_model"),
    ("model inference", "ai_model"),
    ("inference", "ai_model"),
    ("embedding", "ai_model"),
    ("user", "external_actor"),
    ("actor", "external_actor"),
    ("client", "external_actor"),
    ("browser", "external_actor"),
    ("mobile", "external_actor"),
)

_PROVIDER_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("aws", "aws"),
    ("amazon", "aws"),
    ("azure", "azure"),
    ("mscae", "azure"),
    ("gcp", "gcp"),
    ("google", "gcp"),
)


class DrawioParseError(ValueError):
    pass


def parse(data: bytes) -> DesignGraph:
    """Parse a .drawio / .xml export into the common schema."""
    text = data.decode("utf-8", errors="replace")
    models = list(_graph_models(text))
    if not models:
        raise DrawioParseError("No <mxGraphModel> found in the draw.io file.")

    components: list[Component] = []
    connections: list[Connection] = []
    notes: list[str] = []
    seen_ids: set[str] = set()

    for model in models:
        cells = _cells(model)
        for cell_id, label, style, is_edge, source, target in cells:
            if is_edge:
                if source and target:
                    connections.append(
                        Connection(
                            source_id=source,
                            target_id=target,
                            label=label,
                            protocol=_protocol(label, style),
                        )
                    )
                continue

            if not label:
                # Unlabelled shapes carry no reviewable meaning on their own.
                continue
            if cell_id in seen_ids:
                continue
            seen_ids.add(cell_id)

            kind = _classify(label, style)
            if kind == "annotation":
                notes.append(label)
                continue
            components.append(
                Component(
                    id=cell_id,
                    label=label,
                    kind=kind,
                    provider=_provider(label, style),
                    service=_service(style),
                )
            )

    # Drop edges whose endpoints are not components we kept (containers,
    # unlabelled shapes) so the graph stays internally consistent.
    kept = {c.id for c in components}
    connections = [e for e in connections if e.source_id in kept and e.target_id in kept]

    return DesignGraph(
        components=components,
        connections=connections,
        notes=notes,
        source=DiagramSource.DRAWIO,
    )


def _graph_models(text: str) -> list[ET.Element]:
    """Yield every `<mxGraphModel>` in the file, inflating compressed diagrams."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DrawioParseError(f"File is not valid XML: {exc}") from exc

    if root.tag == "mxGraphModel":
        return [root]

    models: list[ET.Element] = []
    for diagram in root.iter("diagram"):
        nested = diagram.find("mxGraphModel")
        if nested is not None:
            models.append(nested)
            continue
        payload = (diagram.text or "").strip()
        if payload:
            inflated = _inflate(payload)
            if inflated is not None:
                models.append(inflated)

    if not models and root.find(".//mxGraphModel") is not None:
        found = root.find(".//mxGraphModel")
        if found is not None:
            models.append(found)
    return models


def _inflate(payload: str) -> ET.Element | None:
    """Undo draw.io's base64 + raw-deflate + URL-encoding compression."""
    try:
        raw = base64.b64decode(payload)
    except (ValueError, TypeError):
        return None
    try:
        # Raw deflate stream: negative window size, no zlib header.
        inflated = zlib.decompress(raw, -zlib.MAX_WBITS)
    except zlib.error:
        try:
            inflated = zlib.decompress(raw)
        except zlib.error:
            return None
    decoded = urllib.parse.unquote(inflated.decode("utf-8", errors="replace"))
    try:
        element = ET.fromstring(decoded)
    except ET.ParseError:
        return None
    return element if element.tag == "mxGraphModel" else element.find(".//mxGraphModel")


def _cells(model: ET.Element) -> list[tuple[str, str, str, bool, str, str]]:
    """Flatten the model into (id, label, style, is_edge, source, target) tuples."""
    out: list[tuple[str, str, str, bool, str, str]] = []

    # `<object>` / `<UserObject>` wrappers carry the label; the inner mxCell
    # carries the geometry and style.
    for wrapper in list(model.iter("object")) + list(model.iter("UserObject")):
        inner = wrapper.find("mxCell")
        if inner is None:
            continue
        label = _clean(wrapper.get("label", ""))
        out.append(
            (
                wrapper.get("id", ""),
                label,
                inner.get("style", ""),
                inner.get("edge") == "1",
                inner.get("source", ""),
                inner.get("target", ""),
            )
        )
        inner.set("_wrapped", "1")

    for cell in model.iter("mxCell"):
        if cell.get("_wrapped") == "1":
            continue
        is_edge = cell.get("edge") == "1"
        if not is_edge and cell.get("vertex") != "1":
            continue
        out.append(
            (
                cell.get("id", ""),
                _clean(cell.get("value", "")),
                cell.get("style", ""),
                is_edge,
                cell.get("source", ""),
                cell.get("target", ""),
            )
        )
    return out


def _clean(value: str) -> str:
    """Strip the HTML draw.io allows inside labels."""
    text = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(html.unescape(text).split())


def _shape_hint(style: str) -> str:
    """The shape/icon names in a style, without draw.io's layout attributes.

    Matching keywords against the raw style string misreads attributes as
    content — `container=1` is a draw.io grouping flag, not a compute container.
    """
    return " ".join(re.findall(r"(?:shape|resIcon)=([\w.]+)", style))


def _classify(label: str, style: str) -> str:
    if "text;" in style or style.startswith("text") or "shape=note" in style:
        return "annotation"
    # Group and container shapes are boundaries regardless of their label.
    if "mxgraph.aws4.group" in style or "container=1" in style:
        return "network_boundary"
    haystack = f"{label} {_shape_hint(style)}".lower()
    for keyword, kind in _KIND_KEYWORDS:
        if keyword in haystack:
            return kind
    return "unknown"


def _provider(label: str, style: str) -> str:
    haystack = f"{label} {style}".lower()
    for keyword, provider in _PROVIDER_KEYWORDS:
        if keyword in haystack:
            return provider
    return "unknown"


def _service(style: str) -> str:
    """Recover the specific service from a shape library reference, if present.

    `resIcon` names the service; `shape` on the same cell is often the generic
    wrapper (`resourceIcon`), so it is only a fallback.
    """
    for attribute in ("resIcon", "shape"):
        match = re.search(rf"{attribute}=mxgraph\.[\w]+\.([\w_]+)", style)
        if match and match.group(1) not in ("resourceIcon", "productIcon", "group"):
            return match.group(1).replace("_", " ")
    return ""


def _protocol(label: str, style: str) -> str:
    haystack = f"{label} {_shape_hint(style)}".lower()
    for token in ("https", "http", "tls", "mtls", "grpc", "sql", "amqp", "mqtt", "ssh", "vpn"):
        if token in haystack:
            return token
    return ""
