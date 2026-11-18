#!/usr/bin/env python3
"""Create reviewable, structured testcase drafts from selected dumpcr files.

This is a category-agnostic extractor. It does not run commands from dumpcr or
claim that extracted bug behavior is a verified expected result.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any


BUG_ID_RE = re.compile(r"CSC[A-Za-z0-9]+\Z")
NOTE_START_RE = re.compile(r"^---Start of Note Titled:\s*(.*?)\s*---$")
NOTE_END_RE = re.compile(r"^---End of Note Titled:")
FIELD_RE = re.compile(
    r"\b(Bug ID|Project|Status|Product|Component|Severity|Headline|Version)\s*:\s*(.*?)"
    r"(?=\s{2,}(?:Bug ID|Project|Status|Product|Component|Severity|Headline|Version)\s*:|$)",
    re.I,
)
STEP_RE = re.compile(r"^\s*(?:Step|Setp)\s*(\d+)(?:\s*\(([^)]*)\))?\s*[:.)-].*$", re.I)
PROMPT_RE = re.compile(r"^\s*[\w./:@-]+(?:\([^)]+\))?[#>]\s+(.+?)\s*$")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DNS_RE = re.compile(r"\b(?=.{4,253}\b)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}\b")
TELNET_URL_RE = re.compile(r"(?i)telnet://[^\s]+")
SECRET_RE = re.compile(
    r"(?i)(\b(?:password|passwd|secret|key)\s*(?::\s*|\s+)(?:[067]\s+)?)"
    r"(?:\"[^\"]*\"|'[^']*'|\S+)"
)
TEST_AAA_RE = re.compile(r"(?i)(\btest\s+aaa\s+group\s+\S+\s+\S+)\s+\S+")
NOTE_TITLE_RE = re.compile(r"repro|unit.?test|test.?plan|r-comments?|release-note|conditions?|workaround", re.I)


def sanitize(text: str) -> str:
    """Normalize dump text and replace common secret/address values."""
    value = html.unescape(re.sub(r"<[^>]+>", "", text))
    value = SECRET_RE.sub(r"\1<REDACTED>", value)
    value = TEST_AAA_RE.sub(r"\1 <REDACTED>", value)
    value = TELNET_URL_RE.sub("<CONSOLE_ENDPOINT>", value)
    value = IP_RE.sub("<IP_ADDRESS>", value)
    value = DNS_RE.sub("<HOSTNAME>", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_notes(lines: list[str]) -> list[dict[str, Any]]:
    notes = []
    index = 0
    while index < len(lines):
        match = NOTE_START_RE.match(lines[index])
        if not match:
            index += 1
            continue
        start = index + 1
        title = match.group(1).strip()
        index += 1
        while index < len(lines) and not NOTE_END_RE.match(lines[index]):
            index += 1
        end = index
        notes.append({"title": title, "start_line": start + 1, "end_line": max(start + 1, end), "lines": lines[start:end]})
        index += 1
    return notes


def extract_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines[:40]:
        for match in FIELD_RE.finditer(line):
            name = match.group(1).lower().replace(" ", "_")
            value = match.group(2).strip()
            # Header columns may share one line; take the field value up to the
            # next known label when that label occurs later on the same line.
            next_field = re.search(
                r"\s{2,}(?:Bug ID|Project|Status|Product|Component|Severity|Headline|Version)\s*:",
                value,
                re.I,
            )
            if next_field:
                value = value[: next_field.start()].strip()
            fields.setdefault(name, sanitize(value))
    return fields


def extract_labeled_sections(lines: list[str]) -> dict[str, str]:
    labels = ("Summary", "Conditions", "Workaround", "Symptom", "Further Problem Description")
    sections: dict[str, str] = {}
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(?:<B>)?([A-Za-z][A-Za-z -]+):(?:</B>)?\s*(.*)$", line.strip(), re.I)
        if not match:
            continue
        label = match.group(1).strip().lower()
        if label not in {item.lower() for item in labels}:
            continue
        chunks = [match.group(2).strip()] if match.group(2).strip() else []
        for following in lines[index + 1 :]:
            if re.match(r"^\s*(?:<B>)?[A-Za-z][A-Za-z -]+:(?:</B>)?", following.strip()):
                break
            if following.startswith("---End of Note") or following.startswith("***"):
                break
            if following.strip():
                chunks.append(following.strip())
        text = sanitize(" ".join(chunks))
        if text:
            sections[label] = text
    return sections


def extract_release_fields(lines: list[str]) -> dict[str, str]:
    releases = {}
    labels = {
        "known-affected-releases": "known_affected_releases",
        "known-fixed-releases": "known_fixed_releases",
        "potentially-affected-releases": "potentially_affected_releases",
    }
    for line in lines[:60]:
        match = re.match(r"^\s*([^:]+):\s*(.*)$", line)
        if match:
            key = match.group(1).strip().lower()
            if key in labels:
                releases[labels[key]] = sanitize(match.group(2))
    return releases


def extract_step_blocks(note: dict[str, Any]) -> list[dict[str, Any]]:
    lines = note["lines"]
    markers = [(i, STEP_RE.match(line)) for i, line in enumerate(lines)]
    markers = [(i, match) for i, match in markers if match]
    blocks = []
    for position, (start, match) in enumerate(markers):
        end = markers[position + 1][0] if position + 1 < len(markers) else len(lines)
        raw = "\n".join(lines[start:end]).strip()
        if raw:
            source_start = note["start_line"] + start
            source_end = min(note["end_line"], source_start + len(raw.splitlines()) - 1)
            blocks.append({
                "step_number": int(match.group(1)),
                "label": (match.group(2) or "").strip(),
                "source_lines": {"start": source_start, "end": source_end},
                "source_excerpt": sanitize(raw)[:1800],
            })
    return blocks


def extract_command_events(note: dict[str, Any]) -> list[dict[str, Any]]:
    """Capture CLI prompt commands and adjacent output without interpreting it."""
    lines = note["lines"]
    prompts = [(i, PROMPT_RE.match(line)) for i, line in enumerate(lines)]
    prompts = [(i, match) for i, match in prompts if match]
    events = []
    for position, (start, match) in enumerate(prompts):
        end = prompts[position + 1][0] if position + 1 < len(prompts) else len(lines)
        command = sanitize(match.group(1))
        if not command or command[0].isdigit():
            continue
        output = sanitize(" ".join(line.strip() for line in lines[start + 1 : end] if line.strip()))
        source_start = note["start_line"] + start
        source_end = note["start_line"] + max(start, end - 1)
        events.append({
            "command": command,
            "observed_output": output[:900],
            "source_lines": {"start": source_start, "end": source_end},
        })
    return events


def build_draft(path: Path, bug_id: str) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    fields = extract_fields(lines)
    if fields.get("bug_id", bug_id).upper() != bug_id.upper():
        raise ValueError("Bug ID in {} does not match requested {}".format(path, bug_id))

    notes = extract_notes(lines)
    sections = extract_labeled_sections(lines)
    release_fields = extract_release_fields(lines)
    relevant_notes = [note for note in notes if NOTE_TITLE_RE.search(note["title"])]
    # Keep selected evidence bounded. Source file and exact line ranges are
    # retained so a reviewer can inspect the full dumpcr text.
    evidence = []
    candidate_steps = []
    for note in relevant_notes:
        steps = extract_step_blocks(note)
        evidence.append({
            "title": note["title"],
            "source_lines": {"start": note["start_line"], "end": note["end_line"]},
            "excerpt": sanitize("\n".join(note["lines"]))[:5000],
            "explicit_steps_found": len(steps),
        })
        candidate_steps.extend(steps)

    summary = sections.get("summary") or sections.get("symptom") or ""
    if not summary or re.fullmatch(r"(?:please\s+)?(?:tac\s+)?repro(?:duction)?\s+detail", summary, re.I):
        summary = fields.get("headline", "")
    conditions = sections.get("conditions", "")
    reproduction_note = next(
        (note for note in relevant_notes if re.search(r"repro", note["title"], re.I)), None
    )
    test_note = next(
        (note for note in relevant_notes if re.search(r"unit.?test|test.?plan", note["title"], re.I)), None
    )

    events = []
    for note in relevant_notes:
        events.extend(extract_command_events(note))

    if candidate_steps:
        steps = [
            {
                "step": index,
                "action": (
                    "Dumpcr Step {} is labeled '{}'; review it as contextual guidance and do not count it as a core reproduction step.".format(item["step_number"], item["label"])
                    if re.search(r"workaround|extra|optional", item["label"], re.I)
                    else "Review and perform the dumpcr's numbered Step {} from the cited source excerpt; adapt lab-specific values to safe test variables.".format(item["step_number"])
                ),
                "expected": "Use the recorded output as observed behavior. Confirm the corrected expected behavior with the testcase reviewer before execution.",
                "source_label": item["label"],
                "source_lines": item["source_lines"],
                "source_excerpt": item["source_excerpt"],
                "review_required": True,
            }
            for index, item in enumerate(candidate_steps, 1)
        ]
    else:
        if events:
            steps = [
                {
                    "step": index,
                    "action": "Review this command from the dumpcr transcript and execute it only if appropriate for the isolated testbed: {}".format(event["command"]),
                    "expected": "Dumpcr recorded this output: {}. Confirm the fixed-behavior assertion separately; transcript output is evidence, not an automatically approved expectation.".format(event["observed_output"] or "(no output captured)"),
                    "source_lines": event["source_lines"],
                    "review_required": True,
                }
                for index, event in enumerate(events[:12], 1)
            ]
        else:
            steps = []
        if not steps:
            source_note = test_note or reproduction_note or (relevant_notes[0] if relevant_notes else None)
            excerpt = sanitize("\n".join(source_note["lines"]))[:1800] if source_note else summary[:1800]
            source_lines = (
                {"start": source_note["start_line"], "end": source_note["end_line"]}
                if source_note else {"start": 1, "end": min(len(lines), 40)}
            )
            steps = [{
                "step": 1,
                "action": "Set up the scenario described in the dumpcr summary/conditions, using isolated test infrastructure and synthetic credentials.",
                "expected": "Prerequisites and exact configuration must be confirmed from the cited dumpcr evidence before execution.",
                "source_lines": source_lines,
                "source_excerpt": excerpt,
                "review_required": True,
            }, {
                "step": 2,
                "action": "Replay the behavior recorded in the dumpcr evidence and capture the relevant CLI/log outputs.",
                "expected": "Compare observed behavior with the report symptom; the fixed-behavior assertion is not explicit enough in this dump to infer automatically.",
                "source_lines": source_lines,
                "source_excerpt": excerpt,
                "review_required": True,
            }]

    commands = list(dict.fromkeys(event["command"] for event in events))[:30]

    prerequisites = []
    if conditions:
        prerequisites.append(conditions)
    if not prerequisites:
        prerequisites.append("Review the cited reproduction notes and establish their lab/testbed prerequisites before running a test.")
    if sections.get("workaround"):
        prerequisites.append("Treat the documented workaround as report context, not as the expected testcase behavior.")

    # Keep transcript procedures as evidence, but define the testcase around
    # the report symptom and an observable fixed-behavior assertion. A V
    # status means the report records verification; it does not mean this
    # generated test has run or that the matching suite has coverage.
    reproduction_context = steps
    verified_bug = fields.get("status", "").strip().upper() == "V"
    observed_failure = sections.get("symptom") or summary or fields.get("headline", "")
    reported_conditions = conditions or "No explicit Conditions section was found; use only prerequisites confirmed by the cited reproduction evidence."
    if verified_bug:
        intended_behavior = "The reported operation succeeds and the documented failure is absent."
        pass_assertion = "Assert that the intended operation completes successfully and that the reported failure signature is absent."
    else:
        intended_behavior = "The fixed behavior is not stated clearly enough in the extracted report to assert automatically."
        pass_assertion = "Do not infer a fixed-behavior pass condition from transcript output alone; identify the expected result from the report's explicit evidence."
    behavior_steps = [
        {
            "step": 1,
            "phase": "setup",
            "action": "Establish only the report conditions needed for the suspected behavior, using isolated test resources and saved pre-test state.",
            "expected": reported_conditions,
        },
        {
            "step": 2,
            "phase": "bug-specific action",
            "action": "Exercise the operation associated with the report symptom: {}".format(fields.get("headline") or summary or "use the operation identified by the cited dumpcr evidence"),
            "expected": "Capture the operation result and the relevant device or service state; reproduction commands below are context to guide this step, not independent coverage criteria.",
        },
        {
            "step": 3,
            "phase": "assertion",
            "action": "Check the intended outcome and the failure signature: {}".format(observed_failure),
            "expected": pass_assertion,
        },
    ]

    return {
        "bugid": bug_id,
        "testcase_id": "TC_{}_DRAFT".format(bug_id),
        "title": fields.get("headline", "Testcase draft for {}".format(bug_id)),
        "status": "generated_behavior_spec",
        "generation": {
            "method": "report_behavior_plus_source_evidence",
            "steps_source": "behavioral_test_plan; dump steps retained as reproduction context",
            "expected_behavior_inferred": verified_bug,
            "codex_reviewed": False,
            "review_note": "This is a draft interpretation, not an execution result. A V status records historical bug verification; it does not by itself define the expected assertion or prove this testcase ran.",
            "assessment_status": "DRAFT_REQUIRES_SOURCE_REVIEW",
        },
        "source": {
            "dumpcr_file": str(path),
            "component": fields.get("component"),
            "product": fields.get("product"),
            "reported_version": fields.get("version"),
            "line_count": len(lines),
            "summary": summary,
            "conditions": conditions,
            "workaround": sections.get("workaround", ""),
            "symptom": sections.get("symptom", ""),
            "evidence_sections": evidence,
        },
        "source_facts": [
            {
                "note": item["title"],
                "source_lines": item["source_lines"],
                "evidence_excerpt": item["excerpt"],
                "kind": "dumpcr evidence; interpretation not added",
            }
            for item in evidence
        ],
        "interpretations": [
            {
                "claim": intended_behavior,
                "basis": "Inferred from the report symptom and available fix/verification excerpts; compare these against the full cited notes.",
                "certainty": "inferred; resolve ambiguities before treating the assertion as authoritative",
            }
        ],
        "release_context": {
            **release_fields,
            "review_note": "Release lists are copied from dumpcr. Check the actual platform/image before a test verdict.",
        },
        "objective": intended_behavior,
        "preconditions": prerequisites,
        "preconditions_and_setup": prerequisites,
        "reported_behavior": {
            "intended_behavior": intended_behavior,
            "observed_incorrect_behavior": observed_failure,
            "conditions_from_report": reported_conditions,
            "fixed_behavior_basis": (
                "inferred from report symptom plus fix/verification evidence; V status alone is not the basis"
                if verified_bug else "not established by extracted report evidence"
            ),
        },
        "bug_specific_action": behavior_steps[1]["action"],
        "expected_after_fix": intended_behavior,
        "failure_condition": observed_failure or "No explicit failure signature extracted; inspect the source evidence.",
        "evidence_to_collect": [
            "Test inputs and configuration needed to establish the report conditions.",
            "Output from the bug-specific operation and the relevant device/service state.",
            "Evidence that the reported failure signature did or did not occur.",
        ],
        "cleanup": "Restore the captured pre-test state and remove only resources created by this testcase.",
        "command_candidates": commands,
        "steps": behavior_steps,
        "reproduction_context": reproduction_context,
        "coverage_requirements": [
            {"id": "report_conditions_reached", "description": "Evidence shows the necessary report conditions were present; mark uncertain conditions explicitly."},
            {"id": "bug_specific_result_asserted", "description": pass_assertion},
        ],
        "pass_criteria": [
            pass_assertion,
            "Test cleanup restores the pre-test configuration and removes only test-owned resources.",
        ],
        "uncertainties": ["The script does not decide whether every transcript detail is essential; keep uncertain transport, count, timing, or configuration details as context unless the report links them to the failure."],
        "safety": "Extracted CLI commands are reference material only; this script never executes commands from a dumpcr. Secrets and addresses are sanitized.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bugids", nargs="+", help="selected IDs, e.g. CSCvq73077 CSCvq95571")
    parser.add_argument("--input-dir", default="bug_info", help="dumpcr directory (default: bug_info)")
    parser.add_argument("--output-dir", default="testcases", help="draft JSON directory (default: testcases)")
    parser.add_argument("--force", action="store_true", help="overwrite existing testcase drafts")
    args = parser.parse_args()

    try:
        bug_ids = list(dict.fromkeys(args.bugids))
        for bug_id in bug_ids:
            if not BUG_ID_RE.fullmatch(bug_id):
                raise ValueError("invalid bug ID: {}".format(bug_id))
        input_dir = Path(args.input_dir)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for bug_id in bug_ids:
            source = input_dir / "bug_{}.txt".format(bug_id)
            output = output_dir / "{}.json".format(bug_id)
            if not source.is_file():
                raise FileNotFoundError("dumpcr file not found: {}".format(source))
            if output.exists() and not args.force:
                raise FileExistsError("{} exists; pass --force to replace it".format(output))
            draft = build_draft(source, bug_id)
            output.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print("Wrote draft for {} to {}".format(bug_id, output))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
