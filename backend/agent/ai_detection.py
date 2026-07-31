"""Deterministic AI/ML-component detection, for the audit trail.

## Why this exists

Eighteen of the rubric's forty-five checks only mean anything if the design has an
AI or ML component in it — `rubric.json` declares which eighteen, per check, via
`ai_conditional`. Whether they applied used to be decided entirely inside the
evaluate stage's `not_applicable` verdict — a per-check model judgement, with no
record of what was looked for, and no way for a reviewer to tell "there is genuinely
no model here" apart from "the model did not notice the model".

That is the failure this module addresses. It produces an independent, reproducible
record of the AI/ML evidence in a design: what was searched for, what matched, and
where. So a wholly-not-applicable pillar can say *why*, and a judge can disagree
with it.

## What it deliberately does NOT do

**It does not move a single verdict, status, severity or score.** The evaluate
stage's `not_applicable` decisions are exactly what they were; `scoring.py` never
sees this record. Two reasons, and the first is the important one:

* a keyword detector is not more trustworthy than the model at deciding whether a
  design has AI in it — it is only more *auditable*. Letting it force a check to be
  evaluated, or to be skipped, would replace a fallible judgement with a fallible
  regex and lose the judgement;
* scoring has to stay reproducible from the rubric and the statuses. Anything that
  can move a score has to be defensible check by check, and "a phrase matched"
  is not that.

So this is evidence for a human, and a **disagreement flag** when the record and
the model's behaviour point opposite ways. Acting on that flag is a person's job.

## No model call

Pure string matching over material the review already holds. It costs nothing, it
returns the same answer every time for the same design, and it can be re-run
against a stored review months later and produce the identical record.

## What the matching learned from the existing keyword map

`ingestion/drawio.py` has a keyword→kind map that includes a bare `"model"`
substring. Measured against 25 labels, that map found 5 of 5 explicitly-labelled
AI components, **0 of 15** implicit ones, and produced **5 false positives out of
5** non-AI labels — "Domain Model", "Cost Model", "Provisioning Model",
"Threat Model Doc" and "Data Model Registry" all classified as `ai_model`.

Both failure modes are designed against here:

* every pattern is anchored on word boundaries, and **no pattern is a bare
  "model"** — the word only appears inside phrases that pin its sense
  ("model registry", "foundation model", "model drift");
* a whole tier exists for capabilities that are almost always ML-implemented but
  are rarely labelled as such ("recommendation engine", "propensity", "churn
  prediction"). Those report as `likely`, not `present`, because the phrase is
  evidence of a *function* and the implementation genuinely might be a rules
  engine.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from schema import AiDetection, AiSignal, DesignGraph

# --------------------------------------------------------------------------- #
# The patterns
#
# Tiered by what a match actually licenses you to conclude, which is not the same
# as how confident the regex is that it matched. A named service is near-proof; a
# generic ML term is strong; a business capability is suggestive and no more.
#
# Every entry is (pattern, what to call it in the record). The label is shown to
# reviewers, so it names the thing found rather than the regex that found it.
# --------------------------------------------------------------------------- #

# Tier 1 — a specific AI/ML product, service or model family. Naming one of these
# is not a description of a capability, it is a statement that a model is present.
_NAMED_SERVICE: tuple[tuple[str, str], ...] = (
    (r"\bbedrock\b", "Amazon Bedrock"),
    (r"\bsagemaker\b", "Amazon SageMaker"),
    (r"\bvertex\s*ai\b", "Google Vertex AI"),
    (r"\bazure\s+(openai|ml|machine\s+learning)\b", "Azure OpenAI / Azure ML"),
    (r"\bopenai\b", "OpenAI"),
    (r"\banthropic\b", "Anthropic"),
    (r"\bclaude\b", "Claude"),
    (r"\bgpt[-\s]?[0-9o]", "GPT model"),
    (r"\bllama\s*[0-9]?\b", "Llama"),
    (r"\bmistral\b", "Mistral"),
    (r"\bgemini\b", "Gemini"),
    (r"\bhugging\s*face\b", "Hugging Face"),
    (r"\bcomprehend\b", "Amazon Comprehend"),
    (r"\brekognition\b", "Amazon Rekognition"),
    (r"\btextract\b", "Amazon Textract"),
    (r"\bamazon\s+personalize\b", "Amazon Personalize"),
    (r"\bamazon\s+forecast\b", "Amazon Forecast"),
    (r"\bkendra\b", "Amazon Kendra"),
    (r"\b(amazon\s+)?lex\b", "Amazon Lex"),
    (r"\btranscribe\b", "Amazon Transcribe"),
    (r"\bpolly\b", "Amazon Polly"),
    (r"\btensorflow\b", "TensorFlow"),
    (r"\bpytorch\b", "PyTorch"),
    (r"\bscikit[-\s]?learn\b", "scikit-learn"),
    (r"\blangchain\b", "LangChain"),
    (r"\bpinecone\b", "Pinecone"),
    (r"\bweaviate\b", "Weaviate"),
    (r"\bmlflow\b", "MLflow"),
    (r"\bkubeflow\b", "Kubeflow"),
)

# Tier 2 — generic AI/ML vocabulary. Not a product name, but not ambiguous either:
# a design does not say "training data" about something that is not being trained.
#
# NOTE the treatment of "model": it appears only inside phrases that fix its
# sense. A bare `\bmodel\b` would match "domain model", "cost model", "threat
# model" and "provisioning model", which is precisely the false-positive class the
# existing drawio keyword map suffers from.
_EXPLICIT_TERM: tuple[tuple[str, str], ...] = (
    (r"\b(machine|deep)\s+learning\b", "machine/deep learning"),
    (r"\bml\b(?!\s*[/-]?\s*s\b)", "ML"),
    (r"\bml[-\s](model|pipeline|ops|platform|workflow)\b", "ML pipeline/platform"),
    (r"\bmlops\b", "MLOps"),
    (r"\ba\.?i\.?[-\s](model|service|component|agent|pipeline|workload)\b", "AI component"),
    (r"\b(generative|gen)\s*ai\b", "generative AI"),
    (r"\bartificial\s+intelligence\b", "artificial intelligence"),
    (r"\bl\.?l\.?m\.?s?\b", "LLM"),
    (r"\blarge\s+language\s+model", "large language model"),
    (r"\bfoundation\s+model", "foundation model"),
    (r"\bneural\s+net", "neural network"),
    (r"\btransformer\s+(model|architecture)\b", "transformer model"),
    (r"\bfine[-\s]?tun", "fine-tuning"),
    (r"\btraining\s+(data|set|job|pipeline|corpus)\b", "training data/job"),
    (r"\btrain(ing|ed)\s+(a\s+)?model\b", "model training"),
    (r"\bmodel\s+(registry|version|card|drift|artifact|weights|endpoint|serving)\b",
     "model registry/serving"),
    (r"\bmodel\s+(training|inference|evaluation|monitoring)\b", "model lifecycle"),
    (r"\bfeature\s+store\b", "feature store"),
    (r"\binference\b", "inference"),
    (r"\bembedding", "embeddings"),
    (r"\bvector\s+(db|database|store|index|search)\b", "vector store"),
    (r"\brag\b", "RAG"),
    (r"\bretrieval[-\s]augmented\b", "retrieval-augmented generation"),
    (r"\bprompt\s+(engineering|template|injection|chain)\b", "prompting"),
    (r"\bsystem\s+prompt\b", "system prompt"),
    (r"\bhallucinat", "hallucination"),
    (r"\bagentic\b", "agentic"),
    (r"\bcopilot\b", "copilot"),
    (r"\bnlp\b|\bnatural\s+language\s+processing\b", "NLP"),
    (r"\bcomputer\s+vision\b", "computer vision"),
    (r"\bocr\b|\boptical\s+character\s+recognition\b", "OCR"),
    (r"\bspeech[-\s]to[-\s]text\b|\btext[-\s]to[-\s]speech\b", "speech/text conversion"),
)

# Tier 3 — a business capability that is almost always ML-implemented and almost
# never says so. These are the reason this module exists: a "Personalization
# Service" box is the realistic shape of an undeclared AI component.
#
# They report `likely`, never `present`. A recommendation engine really can be
# hand-written rules, and calling that AI would be its own kind of wrong.
_IMPLICIT_FUNCTION: tuple[tuple[str, str], ...] = (
    (r"\brecommendation\s+(engine|service|system)\b|\brecommender\b", "recommendation engine"),
    (r"\bpersonali[sz]ation\b|\bpersonali[sz]ed\s+(content|feed|offer|experience)\b",
     "personalisation"),
    (r"\bpropensity\b", "propensity scoring"),
    (r"\bchurn\s+(prediction|predictor|model|score|scoring|risk)\b", "churn prediction"),
    # A component whose job is to emit a score. Deliberately requires a noun that
    # makes it a system ("scoring service") rather than matching the bare word,
    # which appears in sports, games and test-result contexts.
    (r"\bscoring\s+(service|engine|api|endpoint|pipeline|model)\b", "scoring service"),
    (r"\bpredictor\b", "predictor component"),
    (r"\bsentiment\s+analysis\b", "sentiment analysis"),
    (r"\banomaly\s+detection\b", "anomaly detection"),
    (r"\bfraud\s+(detection|scoring|score|model)\b", "fraud detection"),
    (r"\brisk\s+scor(e|ing)\b", "risk scoring"),
    (r"\bcredit\s+scor(e|ing)\b", "credit scoring"),
    (r"\blead\s+scor(e|ing)\b", "lead scoring"),
    (r"\bpredictive\b|\bprediction\s+(service|api|endpoint)\b", "prediction service"),
    (r"\bforecast(ing)?\s+(service|engine|model)\b", "forecasting"),
    (r"\bclassifier\b|\bclassification\s+(service|engine)\b", "classifier"),
    (r"\bsemantic\s+search\b", "semantic search"),
    (r"\bsimilarity\s+(search|matching)\b", "similarity search"),
    (r"\bchat\s?bot\b|\bvirtual\s+(assistant|agent)\b", "chatbot / virtual assistant"),
    (r"\bauto[-\s]?(tagging|categori[sz]ation|classification)\b", "auto-categorisation"),
    (r"\bsummari[sz]ation\s+(service|engine|api)\b|\bauto[-\s]summari", "summarisation"),
    (r"\bnext\s+best\s+(action|offer)\b", "next best action"),
    (r"\bintelligent\s+(routing|triage|matching|document)\b", "intelligent routing/triage"),
    (r"\bsmart\s+(routing|matching|reply|suggest)", "smart routing/suggestion"),
    (r"\branking\s+(service|engine|model)\b", "ranking service"),
    (r"\bmatching\s+(engine|algorithm)\b", "matching engine"),
    (r"\bdemand\s+(forecast|prediction)", "demand forecasting"),
    (r"\bdynamic\s+pricing\b", "dynamic pricing"),
    (r"\bcontent\s+moderation\b", "content moderation"),
    (r"\bentity\s+(extraction|recognition)\b", "entity extraction"),
    (r"\btriage\s+(service|engine|model)\b|\bauto[-\s]?triage\b", "triage service"),
)

# Tier 4 — the design SAYS there is no AI in it.
#
# Recorded as a CLAIM and never as a fact, for exactly the reason the evaluate
# prompt already gives: an assertion inside submitted material is not evidence. Its
# only real use is the contradiction case — a design that says "no AI is used" and
# then names a model is a design whose document and diagram disagree, and that is
# worth a reviewer's attention in a way neither half is alone.
# A bare "no model" is deliberately NOT here, and this was a real bug caught by the
# tests: "No model registry is described" and "No model versioning is described" are
# statements about missing GOVERNANCE — which imply a model EXISTS and is ungoverned,
# the opposite of a denial. Matching them turned a design with Bedrock in it into
# `contradicted`. The `absent` list from the classify stage is full of exactly that
# phrasing, so the loose pattern would have misfired on the field most likely to carry
# a real signal.
#
# So "model" only denies when the sentence is about its EXISTENCE ("no model is
# used", "no model component"), and the negative lookahead on the first pattern keeps
# "no AI governance" — a gap — out of the denial tier for the same reason.
# What a design can be denying when it says it has none of it.
#
# One shared alternation rather than the same list repeated in six regexes: it was
# repeated, and a real SoW slipped through the gap that created. Design B says
#
#     "It does not utilize any foundation models, neural networks, or generative
#      capabilities."
#
# and every denial pattern missed it, because the noun list only knew "ai", "ml"
# and "machine learning". So the sentence formed no denied span, and `_denied_spans`
# could not suppress the positive matches inside it — the words "foundation model"
# and "neural network" were then counted as EVIDENCE OF AI, drawn from the sentence
# saying there is none. A design that stated its position as plainly as possible was
# the one the detector got wrong.
#
# `model` on its own is still absent, and must stay absent: "No model registry is
# described" says a model EXISTS and is ungoverned, which is the opposite of a
# denial. Only phrases that pin the sense — "foundation model", "ml model" — are
# here. See the note above `_DENIAL`.
_DENIED_NOUN = (
    r"(?:a\.?i\.?|ml|artificial\s+intelligence|machine[-\s]learning|"
    r"foundation\s+models?|neural\s+networks?|generative\s+(?:ai|capabilit(?:y|ies))|"
    r"deep\s+learning|l\.?l\.?ms?|large\s+language\s+models?|ml\s+models?)"
)

# Verbs a denial is built on. "utilize" was the other half of the same miss — the
# list held use/using/include/contain, so "does not UTILIZE any foundation models"
# failed on the verb as well as on the noun. Widening one without the other would
# have left the reported sentence still unmatched.
_DENIED_VERB = r"(?:use|using|utilis|utiliz|employ|leverag|includ|contain|involv|require)"

# Nouns that turn "no <AI thing> X" into a GAP statement rather than a denial.
# "No AI governance" means the AI is ungoverned, not that it is absent.
_GOVERNANCE_NOUN = (
    r"(?:governance|registry|monitoring|oversight|review|policy|inventory|"
    r"versioning|audit|owner|documentation|card|drift|strategy|roadmap)"
)

_DENIAL: tuple[tuple[str, str], ...] = (
    (
        rf"\bno\s+{_DENIED_NOUN}\b"
        rf"(?!\s+(?:models?\s+)?{_GOVERNANCE_NOUN})",
        "states no AI/ML",
    ),
    # The comma-list form a real SoW uses: "No model, AI or machine-learning
    # component is used anywhere in this system."
    (r"\bno\s+(\w+[,\s]+){0,4}?(model|ai|ml)[\w,\s-]{0,30}?component\b",
     "states no AI/ML component"),
    (r"\bno\s+models?\s+(is|are)\s+(used|present|involved|deployed)\b",
     "states no model is used"),
    (rf"\bnot?\s+{_DENIED_VERB}\w*\s+(?:any\s+)?{_DENIED_NOUN}\b",
     "states AI/ML not used"),
    (rf"\b{_DENIED_NOUN}\s+(?:is|are)?\s*not\s+(used|present|involved|in\s+scope)\b",
     "states AI/ML not used"),
    (rf"\bwithout\s+(?:any\s+)?{_DENIED_NOUN}\b", "states built without AI/ML"),
)

_TIERS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("named_service", _NAMED_SERVICE),
    ("explicit_term", _EXPLICIT_TERM),
    ("implicit_function", _IMPLICIT_FUNCTION),
    ("denial", _DENIAL),
)

# Compiled once. Case-insensitive throughout: a diagram label may be "BEDROCK",
# "Bedrock" or "bedrock" and all three mean the same thing.
_COMPILED: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (tier, re.compile(pattern, re.IGNORECASE), name)
    for tier, patterns in _TIERS
    for pattern, name in patterns
)

# How much of the surrounding text to keep with a match. Enough to judge whether
# the match means what the detector thinks it means, short enough that a dozen of
# them stay readable.
_EXCERPT_RADIUS = 60

# Per (tier, name), keep at most this many places it was found. "inference"
# appearing in forty paragraphs is one fact, not forty.
_MAX_SITES_PER_SIGNAL = 3


def _excerpt(text: str, start: int, end: int) -> str:
    """The matched phrase with a little context, whitespace collapsed."""
    left = max(0, start - _EXCERPT_RADIUS)
    right = min(len(text), end + _EXCERPT_RADIUS)
    fragment = " ".join(text[left:right].split())
    return f"{'…' if left > 0 else ''}{fragment}{'…' if right < len(text) else ''}"


def _sources(
    graph: DesignGraph | None,
    document_text: str,
    classification: dict[str, Any] | None,
) -> list[tuple[str, str]]:
    """Every piece of text worth searching, paired with where it came from.

    The `where` matters as much as the match. "Found in a diagram box label" and
    "found in the document's list of things the design does NOT do" are very
    different pieces of evidence, and a record that flattened them would be no more
    auditable than the silent not-applicable it replaces.
    """
    out: list[tuple[str, str]] = []

    if graph is not None:
        for component in graph.components:
            described = " ".join(
                part
                for part in (component.label, component.service, component.provider)
                if part and part != "unknown"
            )
            if described.strip():
                out.append((f"diagram component “{component.label or component.id}”", described))
            # The kind is a conclusion something else already reached, so it is
            # recorded separately below rather than pattern-matched as text.
        for connection in graph.connections:
            if connection.label:
                out.append((f"diagram edge “{connection.label}”", connection.label))
        for note in graph.notes:
            out.append(("diagram note", note))

    if document_text.strip():
        out.append(("solution document", document_text))

    if classification:
        if summary := classification.get("design_summary", ""):
            out.append(("classify: design summary", summary))
        for component in classification.get("components", []):
            described = " ".join(
                str(part)
                for part in (
                    component.get("label", ""),
                    component.get("service", ""),
                    component.get("provider", ""),
                )
                if part and part != "unknown"
            )
            if described.strip():
                out.append(
                    (f"classify: component “{component.get('label', '')}”", described)
                )
        for flow in classification.get("data_flows", []):
            if description := flow.get("description", ""):
                out.append(("classify: data flow", description))
        for observation in classification.get("observations", []):
            out.append(("classify: observation", str(observation)))
        # `absent` is searched deliberately. "No model governance is described" is
        # the classify stage saying an AI component exists AND is ungoverned, and
        # dropping it would lose a signal from the one field the classify prompt
        # calls the most important part of its output.
        for absent in classification.get("absent", []):
            out.append(("classify: stated as absent", str(absent)))

    return out


def _classified_ai_kinds(
    graph: DesignGraph | None, classification: dict[str, Any] | None
) -> list[AiSignal]:
    """Components some earlier stage already called `ai_model`.

    Its own tier because it is not a text match: the classify stage (a model) or
    `drawio.py` (a keyword map) reached this conclusion, and the record should say
    which rather than pretending the detector found it.
    """
    out: list[AiSignal] = []
    if graph is not None:
        for component in graph.components:
            if component.kind == "ai_model":
                out.append(
                    AiSignal(
                        tier="classified_kind",
                        signal="component kind is ai_model",
                        source=f"diagram component “{component.label or component.id}”",
                        excerpt=f"{component.label} [kind=ai_model]",
                    )
                )
    for component in (classification or {}).get("components", []):
        if component.get("kind") == "ai_model":
            out.append(
                AiSignal(
                    tier="classified_kind",
                    signal="component kind is ai_model",
                    source=f"classify: component “{component.get('label', '')}”",
                    excerpt=f"{component.get('label', '')} [kind=ai_model]",
                )
            )
    return out


_SENTENCE_BREAK = re.compile(r"[.!?\n;]")


def _denied_spans(text: str) -> list[tuple[int, int]]:
    """Sentence spans that DENY AI/ML, so their contents can be discounted.

    Without this the detector contradicts itself on the commonest possible input. "This
    design does not use machine learning" contains the phrase "machine learning", so
    the denial pattern and the `explicit_term` pattern both match the same eight
    characters, and the record comes out `contradicted` — reporting a design as
    internally inconsistent for the sole reason that it stated its position clearly.

    A mention of AI inside a sentence that denies AI is not evidence of AI. That is the
    entire rule, and the sentence is the unit because it is the smallest span in which
    a negation reliably still applies.
    """
    spans: list[tuple[int, int]] = []
    for tier, pattern, _name in _COMPILED:
        if tier != "denial":
            continue
        for match in pattern.finditer(text):
            left = text.rfind("\n", 0, match.start()) + 1
            for breaker in _SENTENCE_BREAK.finditer(text[:match.start()]):
                left = max(left, breaker.end())
            right_match = _SENTENCE_BREAK.search(text, match.end())
            spans.append((left, right_match.end() if right_match else len(text)))
    return spans


def _scan(sources: Iterable[tuple[str, str]]) -> list[AiSignal]:
    """Every pattern against every source, deduplicated per signal.

    Positive matches falling inside a denying sentence are dropped — see
    `_denied_spans`. Denials themselves are not, since a denial is what created the
    span.
    """
    found: list[AiSignal] = []
    seen: dict[tuple[str, str], int] = {}

    for where, text in sources:
        if not text:
            continue
        denied = _denied_spans(text)
        for tier, pattern, name in _COMPILED:
            for match in pattern.finditer(text):
                if tier != "denial" and any(
                    start <= match.start() < end for start, end in denied
                ):
                    continue
                key = (tier, name)
                if seen.get(key, 0) >= _MAX_SITES_PER_SIGNAL:
                    break
                seen[key] = seen.get(key, 0) + 1
                found.append(
                    AiSignal(
                        tier=tier,
                        signal=name,
                        source=where,
                        excerpt=_excerpt(text, match.start(), match.end()),
                    )
                )
    return found


def detect(
    graph: DesignGraph | None,
    document_text: str = "",
    classification: dict[str, Any] | None = None,
) -> AiDetection:
    """Build the AI/ML evidence record for one design.

    Deterministic and free. Nothing here reads or writes a verdict.
    """
    signals = _classified_ai_kinds(graph, classification)
    signals += _scan(_sources(graph, document_text, classification))

    # Ordered strongest-first so the record reads as an argument rather than as a
    # dump: what proves it, then what suggests it, then what denies it.
    order = {"classified_kind": 0, "named_service": 1, "explicit_term": 2,
             "implicit_function": 3, "denial": 4}
    signals.sort(key=lambda s: (order.get(s.tier, 9), s.signal, s.source))

    return AiDetection(
        signals=signals,
        patterns_checked=len(_COMPILED),
        components_seen=_component_labels(graph, classification),
    )


def _component_labels(
    graph: DesignGraph | None, classification: dict[str, Any] | None
) -> list[str]:
    """The design's component labels, so "nothing found" can be checked against
    what was actually looked at.

    This is the "Components found: [...]" half of an auditable not-applicable. A
    reviewer who sees `absent` next to a list containing "Personalization Service"
    can overrule the record immediately; without the list they would have to take
    it on trust.
    """
    labels: list[str] = []
    seen: set[str] = set()
    for label in [c.label or c.id for c in (graph.components if graph else [])] + [
        str(c.get("label") or c.get("id") or "")
        for c in (classification or {}).get("components", [])
    ]:
        cleaned = " ".join(label.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            labels.append(cleaned)
    return labels
