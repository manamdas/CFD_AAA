#!/usr/bin/env python3
"""Build source-derived testcase drafts, review their TaskLog evidence, and rebuild the dashboard.

The script validates the Codex-reviewed behavior-to-log mappings recorded in
coverage_results.json. It checks cited line ranges and testcase UID/result
markers, but does not infer semantic coverage from keywords.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from draft_testcases_from_dumpcr import BUG_ID_RE, build_draft

PROJECT_DIR = Path(__file__).resolve().parent


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def validate_source_refs(testcase: dict[str, Any], project_dir: Path) -> list[str]:
    problems = []
    source = testcase.get("source", {})
    references = list(source.get("references", []))
    for fact in testcase.get("source_facts", []):
        span = fact.get("lines")
        if not span and fact.get("source_lines"):
            source_lines = fact["source_lines"]
            span = [source_lines.get("start"), source_lines.get("end")]
        references.append({
            "note": fact.get("note", "source fact"),
            "file": fact.get("source", source.get("dumpcr_file", "")),
            "lines": span or [],
        })
    for reference in references:
        ref_file = reference.get("file", reference.get("path", ""))
        source_path = project_dir / ref_file
        if not source_path.is_file():
            # Current specs commonly point to the shared bug_info folder.
            source_path = project_dir / source.get("dumpcr_file", "")
        if not source_path.is_file():
            problems.append("missing dumpcr source for reference {}".format(reference.get("note", "unknown")))
            continue
        line_count = sum(1 for _ in source_path.open(encoding="utf-8", errors="replace"))
        span = reference.get("lines", [])
        if len(span) != 2 or span[0] < 1 or span[1] < span[0] or span[1] > line_count:
            problems.append("invalid dumpcr line range {} in {}".format(span, source_path.name))
    return problems


def validate_log_mapping(mapping: dict[str, Any], log_lines: list[str]) -> dict[str, Any]:
    span = mapping.get("log_lines") or mapping.get("lines")
    uid = mapping.get("uid") or mapping.get("testcase")
    if not uid or not isinstance(span, list) or len(span) != 2:
        return {"uid": uid, "result": "UNVERIFIED", "problem": "mapping lacks UID or line range"}
    start, end = span
    if start < 1 or end < start or end > len(log_lines):
        return {"uid": uid, "result": "UNVERIFIED", "problem": "TaskLog line range is outside the file"}
    block = log_lines[start - 1:end]
    start_marker = any("Starting testcase {}".format(uid) in line for line in block[:8])
    result_markers = [line for line in block if "result of testcase {} is =>".format(uid) in line]
    if not start_marker:
        return {"uid": uid, "result": "UNVERIFIED", "problem": "UID start marker absent from cited range"}
    if not result_markers:
        return {"uid": uid, "result": "UNVERIFIED", "problem": "UID result marker absent from cited range"}
    result = "PASS" if "PASSED" in result_markers[-1] else "FAIL" if "FAILED" in result_markers[-1] else "UNVERIFIED"
    return {"uid": uid, "result": result, "source": "TaskLog.Task-1", "log_lines": span}


def assess_bug(record: dict[str, Any], testcase: dict[str, Any], log_lines: list[str]) -> dict[str, Any]:
    mappings = [validate_log_mapping(mapping, log_lines) for mapping in record.get("suite_mappings", [])]
    results = [item["result"] for item in mappings]
    raw_exercise = record.get("log_exercise_status", "UNVERIFIED")
    exercise = "MATCHES_CENTRAL_BEHAVIOR" if raw_exercise.startswith("LOG_MATCHES_BEHAVIOR") else raw_exercise
    if exercise == "MATCHES_CENTRAL_BEHAVIOR" and mappings and all(result == "PASS" for result in results):
        status = "PASS"
        rationale = "Codex-reviewed log context matches the testcase's central behavior and the candidate testcase passed."
    elif exercise == "NOT_EXERCISED" and mappings and all(result == "PASS" for result in results):
        status = "NOT_EXERCISED"
        rationale = "The related candidate test(s) passed, but the log does not show the testcase's bug-specific trigger and assertion."
    else:
        status = "UNVERIFIED"
        rationale = "The cited log evidence or testcase-to-behavior review is incomplete."
    source_problems = validate_source_refs(testcase, PROJECT_DIR)
    testcase_criteria = {
        criterion.get("id")
        for criterion in testcase.get("coverage_requirements", [])
        if criterion.get("id")
    }
    required_criteria = {
        criterion.get("id")
        for criterion in record.get("required_criteria", [])
        if criterion.get("id")
    }
    if required_criteria and not required_criteria.issubset(testcase_criteria):
        source_problems.append("coverage profile criteria do not match testcase criteria")
    if source_problems:
        status = "UNVERIFIED"
        rationale = "Dumpcr source references failed validation: {}".format("; ".join(source_problems))
    # Any missing/invalid candidate evidence blocks an asserted PASS. A
    # NOT_EXERCISED assessment remains meaningful only when the cited tests ran.
    if any(item["result"] == "UNVERIFIED" for item in mappings):
        status = "UNVERIFIED"
        rationale = "One or more cited TaskLog UID/result ranges could not be verified."
    candidate_execution = "PASS" if mappings and all(result == "PASS" for result in results) else (
        "FAIL" if "FAIL" in results else "UNVERIFIED" if mappings else "NOT_RUN"
    )
    criteria = [criterion.get("id") for criterion in record.get("required_criteria", [])]
    matched = criteria if status == "PASS" else []
    return {
        "status": status,
        "assessment_stage": "CODEX_REVIEWED_LOG_DRAFT",
        "review_method": "Central behavior and assertions reviewed against surrounding TaskLog context; UID/result and source references validated by script.",
        "rationale": rationale,
        "log_exercise_status": exercise,
        "matched_criteria": matched,
        "unmatched_criteria": [] if status == "PASS" else criteria,
        "candidate_tests": mappings,
        "candidate_execution_result": candidate_execution,
        "generated_testcase_execution_result": "NOT_RUN",
        "limitation": "This is a log-based draft coverage assessment. The generated testcase itself was not executed; candidate suite runs are the evidence source.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bugids", nargs="*", help="Bug IDs; defaults to current scored scope")
    parser.add_argument("--input-dir", default="bug_info")
    parser.add_argument("--output-dir", default="testcases")
    parser.add_argument("--log", default="TaskLog.Task-1")
    parser.add_argument("--coverage", default="coverage_results.json")
    parser.add_argument("--force-drafts", action="store_true", help="replace existing testcase JSON with a newly extracted draft")
    parser.add_argument("--no-dashboard", action="store_true", help="do not rebuild dashboard.json")
    args = parser.parse_args()

    try:
        input_dir = (PROJECT_DIR / args.input_dir).resolve()
        output_dir = (PROJECT_DIR / args.output_dir).resolve()
        log_path = (PROJECT_DIR / args.log).resolve()
        coverage_path = (PROJECT_DIR / args.coverage).resolve()
        if not log_path.is_file():
            raise FileNotFoundError("suite log not found: {}".format(log_path))
        coverage = load_json(coverage_path)
        records = {record["bugid"]: record for record in coverage.get("bugs", [])}
        bugids = list(dict.fromkeys(args.bugids or coverage.get("scope", [])))
        if not bugids:
            raise ValueError("no bug IDs supplied and coverage scope is empty")
        output_dir.mkdir(parents=True, exist_ok=True)
        log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

        for bugid in bugids:
            if not BUG_ID_RE.fullmatch(bugid):
                raise ValueError("invalid bug ID: {}".format(bugid))
            dump_path = input_dir / "bug_{}.txt".format(bugid)
            testcase_path = output_dir / "{}.json".format(bugid)
            if not dump_path.is_file():
                raise FileNotFoundError("dumpcr not found: {}".format(dump_path))
            if args.force_drafts or not testcase_path.is_file():
                testcase = build_draft(dump_path, bugid)
                testcase_path.write_text(json.dumps(testcase, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            else:
                testcase = load_json(testcase_path)
            if testcase.get("bugid") != bugid:
                raise ValueError("testcase bug ID mismatch: {}".format(testcase_path))
            record = records.get(bugid)
            if not record:
                record = {"bugid": bugid, "status": "UNVERIFIED", "required_criteria": [], "suite_mappings": []}
                coverage.setdefault("scope", []).append(bugid)
                coverage.setdefault("bugs", []).append(record)
                records[bugid] = record
            draft = assess_bug(record, testcase, log_lines)
            record["status"] = draft["status"]
            record["log_exercise_status"] = draft["log_exercise_status"]
            record["assessment_status"] = draft["assessment_stage"]
            record["testcase_status"] = testcase.get("testcase_status", testcase.get("status", "DRAFT"))
            record["testcase_file"] = str(testcase_path.relative_to(PROJECT_DIR))
            record["coverage_basis"] = "Codex-reviewed draft comparison of central testcase behavior with TaskLog evidence. Suite source is unavailable, so this remains a draft assessment."
            record["draft_coverage"] = draft
            record["covered_criteria"] = draft["matched_criteria"]
            record["missing_criteria"] = draft["unmatched_criteria"]
            record["reasons"] = [draft["rationale"], draft["limitation"]]
            for mapping in record.get("suite_mappings", []):
                mapping["covers_criteria"] = draft["matched_criteria"] if draft["status"] == "PASS" else []
            record["latest_run"] = {
                "source": log_path.name,
                "run_id": coverage.get("suite", {}).get("run_id"),
                "execution_result": draft["candidate_execution_result"],
                "generated_testcase_execution": draft["generated_testcase_execution_result"],
                "bug_behavior_in_log": draft["log_exercise_status"],
                "note": draft["rationale"],
            }
            testcase["suite_log_assessment"] = draft
            if isinstance(testcase.get("suite"), dict):
                testcase["suite"]["source_availability"] = "Suite source was not supplied; this is a Codex-reviewed draft assessment from TaskLog. The generated testcase itself remains NOT_RUN."
            if isinstance(testcase.get("suite_log_scope"), dict):
                testcase["suite_log_scope"]["scope_note"] = "TaskLog.Task-1 was reviewed for a draft behavior match. Suite source was unavailable, and the generated testcase itself was not run."
            testcase_path.write_text(json.dumps(testcase, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print("{}: coverage={} execution={} testcase={}".format(
                bugid, draft["status"], draft["candidate_execution_result"], draft["generated_testcase_execution_result"]
            ))

        coverage["scope"] = list(dict.fromkeys(coverage.get("scope", [])))
        coverage["scope_count"] = len(coverage["scope"])
        statuses = [records.get(bugid, {}).get("status", "UNVERIFIED") for bugid in coverage["scope"]]
        coverage["coverage_pass_count"] = statuses.count("PASS")
        coverage["coverage_fail_count"] = statuses.count("FAIL")
        coverage["coverage_unverified_count"] = statuses.count("UNVERIFIED")
        coverage["coverage_not_exercised_count"] = statuses.count("NOT_EXERCISED")
        coverage["coverage_percentage"] = round(100.0 * coverage["coverage_pass_count"] / coverage["scope_count"], 2) if coverage["scope_count"] else 0.0
        coverage["assessment_stage"] = "CODEX_REVIEWED_LOG_DRAFT"
        coverage["score_note"] = "Draft coverage score = log-supported PASS / all in-scope bugs. NOT_EXERCISED and UNVERIFIED remain separate; generated testcase run status is NOT_RUN unless run evidence is added."
        coverage["suite_code_status"] = "NOT_REQUIRED_FOR_DRAFT_LOG_ASSESSMENT; source code unavailable, so results remain draft."
        coverage["review_policy"] = "This draft coverage is based on Codex review of testcase behavior against the archived TaskLog. It is not a device execution of the generated draft and should not be represented as a final source-code assertion audit."
        if isinstance(coverage.get("suite"), dict):
            coverage["suite"]["coverage_method"] = "Codex-reviewed central behavior compared with surrounding TaskLog setup, actions, assertions, cleanup and result; the pipeline validates cited ranges and result markers."
        coverage_path.write_text(json.dumps(coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if not args.no_dashboard:
            subprocess.run([sys.executable, str(PROJECT_DIR / "build_dashboard.py"), "--coverage", str(coverage_path)], cwd=str(PROJECT_DIR), check=True)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
