"""Guards on the deployment-verification script's formatter.

`scripts/verify_docker_service.sh` is how a deployment gets checked by hand, so
when it breaks it breaks at exactly the moment someone is trying to find out
whether a deploy worked. It broke that way once already: the report was written
as an inline `python3 -c` string, which forced backslash-escaped quotes inside
f-string expressions, and that is a SyntaxError before Python 3.12. The review
had completed fine; only the report died — the worst possible failure mode,
because it looks like the service is broken when it is not.

So the formatting lives in `scripts/verify_docker_service.py` and is pinned here:

* it parses and imports under the interpreter running the tests, which is the
  check the inline version could not have;
* every branch of the fidelity block renders — the three numbers are reported
  separately and the OCR line stays labelled as an estimate;
* the shell script delegates rather than re-embedding Python.

Nothing here talks to a service. This tests the reporting, not the pipeline.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPT_PY = REPO / "scripts" / "verify_docker_service.py"
SCRIPT_SH = REPO / "scripts" / "verify_docker_service.sh"


def _load() -> Any:
    """Import the formatter from scripts/, which is not an importable package."""
    spec = importlib.util.spec_from_file_location("verify_docker_service", SCRIPT_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="fmt")
def fmt_fixture() -> Any:
    return _load()


def _capture(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, payload: Any, *argv: str
) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    fmt.main(["verify_docker_service.py", *argv])
    return capsys.readouterr().out


def _result(**overrides: Any) -> dict:
    """A minimal complete review body, shaped like the real /reviews/{id} response."""
    result = {
        "title": "Order intake platform",
        "overall_score": 88.6,
        "components": [{"name": "api"}] * 9,
        "token_usage": {"input": 1, "output": 2},
        "frameworks": [{"framework_name": "AWS Well-Architected Framework", "score": 77.2}],
        "warnings": [],
        "executive_summary": "One line.",
        "findings": [
            {
                "check_id": "rel_dr_plan",
                "status": "fail",
                "severity": "medium",
                "priority": 1,
                "title": "A disaster recovery approach is present.",
            }
        ],
        "fidelity": {},
    }
    result.update(overrides)
    return result


# --------------------------------------------------------------------------- #
# the regression that actually happened


def test_the_formatter_parses_under_this_interpreter() -> None:
    """The inline-`python3 -c` version raised SyntaxError on Python < 3.12.

    Importing is the whole assertion: a syntax error in the report cannot reach
    the tester if the module has to import before the suite passes.
    """
    assert _load() is not None


def test_the_shell_script_does_not_embed_python_source() -> None:
    """Delegation is the fix. Re-embedding would reintroduce the escaping trap."""
    shell = SCRIPT_SH.read_text()
    assert "verify_docker_service.py" in shell
    # Comments are allowed to name the trap; code is not allowed to fall into it.
    code = [line for line in shell.splitlines() if not line.lstrip().startswith("#")]
    assert "python3 -c" not in "\n".join(code)
    for line in code:
        if "python3" in line:
            assert '"$FMT"' in line, line


# --------------------------------------------------------------------------- #
# the fidelity block — the reason the script exists


def test_a_measured_ocr_proxy_reports_the_number_and_stays_labelled_an_estimate(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    out = _capture(
        fmt,
        monkeypatch,
        capsys,
        _result(
            fidelity={
                "structural": None,
                "ocr_proxy": {
                    "available": True,
                    "unavailable_reason": "",
                    "is_estimate": True,
                    "ocr_tokens": 39,
                    "matched_tokens": 20,
                    "percent": 51.3,
                    "sample_unmatched": ["dlq", "audit"],
                },
                "grounding": None,
            }
        ),
        "report",
    )
    assert "51.3" in out
    assert "20/39" in out
    assert "ESTIMATED" in out
    assert "TESSERACT IS WORKING" in out
    assert "dlq, audit" in out


def test_an_unavailable_ocr_proxy_says_not_measured_rather_than_zero(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """`available: false` is not 0% coverage, and must never print as a number."""
    out = _capture(
        fmt,
        monkeypatch,
        capsys,
        _result(
            fidelity={
                "structural": None,
                "ocr_proxy": {
                    "available": False,
                    "unavailable_reason": "tesseract is not installed",
                    "is_estimate": True,
                    "ocr_tokens": 0,
                    "matched_tokens": 0,
                    "percent": 0.0,
                    "sample_unmatched": [],
                },
                "grounding": None,
            }
        ),
        "report",
    )
    assert "NOT MEASURED" in out
    assert "tesseract is not installed" in out
    assert "0.0%" not in out
    assert "~0%" not in out


def test_structural_coverage_is_reported_as_exact_not_estimated(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    out = _capture(
        fmt,
        monkeypatch,
        capsys,
        _result(
            fidelity={
                "structural": {
                    "percent": 91.7,
                    "parsed_elements": 11,
                    "total_elements": 12,
                    "dropped": ["1 shape with no label"],
                },
                "ocr_proxy": None,
                "grounding": {"checked": 8, "removed": 2},
            }
        ),
        "report",
    )
    assert "91.7%  EXACT" in out
    assert "11/12" in out
    assert "1 shape with no label" in out
    assert "2 ungrounded claims caught and removed" in out
    assert "of 8 returned" in out


def test_the_three_numbers_are_never_combined_into_one(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """No composite. Each line is its own number with its own honesty label."""
    out = _capture(
        fmt,
        monkeypatch,
        capsys,
        _result(
            fidelity={
                "structural": {
                    "percent": 90.0,
                    "parsed_elements": 9,
                    "total_elements": 10,
                    "dropped": [],
                },
                "ocr_proxy": {
                    "available": True,
                    "unavailable_reason": "",
                    "is_estimate": True,
                    "ocr_tokens": 10,
                    "matched_tokens": 5,
                    "percent": 50.0,
                    "sample_unmatched": [],
                },
                "grounding": {"checked": 4, "removed": 1},
            }
        ),
        "report",
    )
    fidelity = out.split("DATA FIDELITY")[1]
    assert fidelity.count("structural") == 1
    assert fidelity.count("OCR proxy") == 1
    assert fidelity.count("grounding") == 1
    for combined in ("overall fidelity", "combined", "average fidelity", "70.0"):
        assert combined not in fidelity


def test_an_empty_fidelity_block_still_renders_all_three_lines(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """An older review, stored before fidelity existed, must not crash the report."""
    out = _capture(fmt, monkeypatch, capsys, _result(fidelity=None), "report")
    assert "structural   not applicable" in out
    assert "OCR proxy    not applicable" in out
    assert "grounding    no filter ran" in out


# --------------------------------------------------------------------------- #
# the other subcommands the shell depends on


def test_stages_emits_one_pipe_delimited_line_per_stage_then_the_state(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    out = _capture(
        fmt,
        monkeypatch,
        capsys,
        {
            "state": "running",
            "stages": [
                {"name": "ingest", "state": "done", "detail": "9 components"},
                {"name": "screen", "state": "running", "detail": None},
            ],
        },
        "stages",
    )
    assert out.splitlines() == [
        "done|ingest|9 components",
        "running|screen|",
        "STATE|running|",
    ]


def test_a_detail_containing_a_pipe_cannot_break_the_shell_split(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The shell reads these with IFS='|', so a pipe in model-derived text would
    silently truncate the line it appears in."""
    out = _capture(
        fmt,
        monkeypatch,
        capsys,
        {
            "state": "error",
            "error": "provider said a|b",
            "stages": [{"name": "evaluate", "state": "error", "detail": "x|y"}],
        },
        "stages",
    )
    assert out.splitlines() == ["error|evaluate|x/y", "STATE|error|provider said a/b"]


def test_a_rejection_object_is_reported_as_its_message(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """`rejection` is an object on the wire while `error` is a string; printing the
    dict would put a repr in front of the tester."""
    out = _capture(
        fmt,
        monkeypatch,
        capsys,
        {
            "state": "rejected",
            "rejection": {"code": "unrelated", "message": "This looks like a resume."},
            "stages": [],
        },
        "stages",
    )
    assert out.splitlines() == ["STATE|rejected|This looks like a resume."]


def test_field_walks_nested_keys_and_prints_a_blank_line_when_absent(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    assert _capture(fmt, monkeypatch, capsys, {"key": "uploads/a/b.md"}, "field", "key") == (
        "uploads/a/b.md\n"
    )
    assert _capture(fmt, monkeypatch, capsys, {"a": {"b": 7}}, "field", "a", "b") == "7\n"
    assert _capture(fmt, monkeypatch, capsys, {"a": 1}, "field", "missing") == "\n"
    assert _capture(fmt, monkeypatch, capsys, {"a": 1}, "field", "a", "b") == "\n"


def test_a_non_json_body_yields_a_blank_field_rather_than_a_traceback(
    fmt: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """A gateway HTML error page must produce an empty key the shell can check for,
    not a stack trace the tester has to interpret."""
    monkeypatch.setattr("sys.stdin", io.StringIO("<html>502 Bad Gateway</html>"))
    assert fmt.main(["verify_docker_service.py", "field", "key"]) == 0
    assert capsys.readouterr().out == "\n"


def test_request_builds_the_reviews_body_from_the_two_upload_keys(
    fmt: Any, capsys: Any
) -> None:
    fmt.main(["verify_docker_service.py", "request", "uploads/a/sow.md", "uploads/b/d.png"])
    body = json.loads(capsys.readouterr().out)
    assert body["document_key"] == "uploads/a/sow.md"
    assert body["diagram_key"] == "uploads/b/d.png"
    assert body["title"]


def test_an_unknown_subcommand_exits_nonzero(fmt: Any) -> None:
    assert fmt.main(["verify_docker_service.py", "summarise"]) == 2
    assert fmt.main(["verify_docker_service.py"]) == 2
