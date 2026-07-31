#!/usr/bin/env python3
"""Formatting helpers for scripts/verify_docker_service.sh.

Everything here reads one JSON document on stdin and writes plain text on stdout.
It exists as a file rather than as `python3 -c '...'` inside the shell script for
two reasons:

  * quoting. The report needs `"` inside f-string expressions to index dicts, and
    before Python 3.12 a backslash is illegal inside an f-string expression part,
    so the escaping a shell heredoc-free `-c` string forces on you is not merely
    ugly, it fails to parse. Moving the code into a file removes the constraint
    entirely.
  * it is testable. See backend/tests/test_verify_script.py.

Subcommands:
  field KEY [KEY...]  print one nested value, or an empty line if it is absent
  request SOW DIAGRAM print the POST /reviews body (no stdin)
  stages              one `state|name|detail` line per stage, then a STATE line
  report              the human-readable result, including the fidelity numbers
"""

from __future__ import annotations

import collections
import json
import sys


def _load() -> object:
    try:
        return json.load(sys.stdin)
    except Exception:  # a non-JSON body is the caller's problem to report
        return None


def field(keys: list[str]) -> int:
    """Walk `keys` into the stdin document. Absent or non-dict -> empty line."""
    data = _load()
    for key in keys:
        if not isinstance(data, dict):
            data = None
            break
        data = data.get(key)
    print("" if data is None else data)
    return 0


def request(document_key: str, diagram_key: str) -> int:
    print(
        json.dumps(
            {
                "document_key": document_key,
                "diagram_key": diagram_key,
                "title": "Order intake platform (deployment verification)",
            }
        )
    )
    return 0


def stages() -> int:
    """Emit the stage table as pipe-delimited lines for the shell to diff."""
    status = _load()
    if not isinstance(status, dict):
        return 1
    for entry in status.get("stages") or []:
        name = entry.get("name", "")
        detail = (entry.get("detail") or "").replace("|", "/")
        print(f'{entry.get("state", "")}|{name}|{detail}')
    terminal = status.get("error") or status.get("rejection") or ""
    if isinstance(terminal, dict):  # rejection is an object, error is a string
        terminal = terminal.get("message", "")
    print(f'STATE|{status.get("state", "")}|{str(terminal).replace("|", "/")}')
    return 0


def _fidelity(fid: dict) -> None:
    """The three numbers. Printed separately, never combined into one score."""
    print("\n  DATA FIDELITY — three separate numbers, never blended:")

    structural = fid.get("structural")
    if structural is None:
        print("    structural   not applicable (image path — a .drawio reports this)")
    else:
        print(
            f'    structural   {structural["percent"]}%  EXACT  '
            f'({structural["parsed_elements"]}/{structural["total_elements"]} elements)'
        )
        for reason in structural.get("dropped") or []:
            print(f"                 - {reason}")

    ocr = fid.get("ocr_proxy")
    if ocr is None:
        print("    OCR proxy    not applicable (no image was uploaded)")
    elif not ocr.get("available"):
        print(f'    OCR proxy    NOT MEASURED — {ocr.get("unavailable_reason", "")}')
        print("                 ^ on the NATIVE runtime this is expected: no tesseract")
        print("                   binary. On the DOCKER service it means something is wrong.")
    else:
        print(
            f'    OCR proxy    ~{ocr["percent"]}%  ESTIMATED  '
            f'({ocr["matched_tokens"]}/{ocr["ocr_tokens"]} OCR-read words found)'
        )
        print(f'                 is_estimate={ocr["is_estimate"]}')
        unmatched = ocr.get("sample_unmatched") or []
        if unmatched:
            print(
                "                 unmatched (missed labels OR OCR noise): "
                + ", ".join(unmatched)
            )
        print("                 ^ TESSERACT IS WORKING in this deployment.")

    grounding = fid.get("grounding")
    if grounding is None:
        print("    grounding    no filter ran (no submitter context to ground against)")
    else:
        print(
            f'    grounding    {grounding["removed"]} ungrounded claims caught and '
            f'removed (of {grounding["checked"]} returned)'
        )


def report() -> int:
    result = _load()
    if not isinstance(result, dict):
        print("  the response body was not a JSON object", file=sys.stderr)
        return 1

    findings = result.get("findings") or []
    by_status = collections.Counter(f.get("status", "?") for f in findings)

    print(f'  title            {result.get("title", "")}')
    print(f'  overall score    {result.get("overall_score")} / 100')
    print(f"  findings         {len(findings)} checks  {dict(by_status)}")
    print(f'  components       {len(result.get("components") or [])}')
    print(f'  token usage      {result.get("token_usage") or {}}')

    print("\n  framework scores:")
    for framework in result.get("frameworks") or []:
        print(f'    {framework["framework_name"]:<34} {framework["score"]:>6}')

    warnings = result.get("warnings") or []
    print(f"\n  extraction warnings: {len(warnings)}")
    for warning in warnings:
        print(f'    [{warning["code"]}] {warning["message"][:88]}')

    _fidelity(result.get("fidelity") or {})

    print("\n  executive summary:")
    for line in (result.get("executive_summary") or "(none)").splitlines():
        print(f"    {line}")

    print("\n  top 3 findings by priority:")
    # priority 0 means unprioritised, so it sorts last rather than first.
    ranked = sorted(findings, key=lambda f: (f.get("priority") == 0, f.get("priority")))
    for finding in ranked[:3]:
        print(
            f'    [{finding["priority"]}] {finding["severity"].upper():<6} '
            f'{finding["check_id"]}  ({finding["status"]})'
        )
        print(f'        {finding["title"][:96]}')
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command, rest = argv[1], argv[2:]
    if command == "field":
        return field(rest)
    if command == "request":
        return request(*rest)
    if command == "stages":
        return stages()
    if command == "report":
        return report()
    print(f"unknown subcommand: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
