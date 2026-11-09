#!/usr/bin/env python3
"""Build dashboard JSON from dumpcr text files in bug_info/."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


BUG_ID_RE = re.compile(r"^\s*Bug ID\s*:\s*(CSC[A-Za-z0-9]+)", re.IGNORECASE)


def extract_bug_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    bug_id = None
    release_changes: list[tuple[str, str, str]] = []

    for line in text.splitlines():
        bug_match = BUG_ID_RE.match(line)
        if bug_match and bug_id is None:
            bug_id = bug_match.group(1)

        # The date and user share the first column, followed by field, old, new, action.
        columns = [column.strip() for column in line.split("\t")]
        if len(columns) >= 5 and columns[1] == "Integrated-releases":
            release_changes.append((columns[2], columns[3], columns[4]))

    if bug_id is None:
        filename_match = re.match(r"bug_(CSC[A-Za-z0-9]+)\.txt\Z", path.name)
        if filename_match:
            bug_id = filename_match.group(1)
    if bug_id is None:
        raise ValueError(f"could not find Bug ID in {path}")

    # dumpcr lists history newest first. Replay it oldest to newest to derive
    # the currently integrated releases, honoring additions and deletions.
    integrated_releases: list[str] = []
    for old_value, new_value, action in reversed(release_changes):
        if action.lower() in {"del", "mod"} and old_value:
            integrated_releases = [
                release for release in integrated_releases if release != old_value
            ]
        if action.lower() in {"new", "mod"} and new_value:
            if new_value not in integrated_releases:
                integrated_releases.append(new_value)

    return {"bugid": bug_id, "integrated_releases": integrated_releases}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract bug IDs and current integrated releases for a dashboard."
    )
    parser.add_argument(
        "--input-dir",
        default="bug_info",
        help="directory containing dumpcr text files (default: bug_info)",
    )
    parser.add_argument(
        "--output",
        default="dashboard.json",
        help="dashboard JSON output path (default: dashboard.json)",
    )
    args = parser.parse_args()

    try:
        input_dir = Path(args.input_dir)
        paths = sorted(input_dir.glob("bug_CSC*.txt"))
        if not paths:
            raise FileNotFoundError(f"no bug_CSC*.txt files found in {input_dir}")

        bugs = [extract_bug_data(path) for path in paths]
        testcase_dir = Path(__file__).resolve().parent / "testcases"
        for bug in bugs:
            testcase_path = testcase_dir / "{}.json".format(bug["bugid"])
            if testcase_path.is_file():
                with testcase_path.open(encoding="utf-8") as testcase_file:
                    testcase = json.load(testcase_file)
                if testcase.get("bugid") != bug["bugid"]:
                    raise ValueError(
                        "testcase bugid mismatch in {}".format(testcase_path)
                    )
                bug["testcase"] = testcase
        dashboard = {"total_bugs": len(bugs), "bugs": bugs}
        output_path = Path(args.output)
        output_path.write_text(
            json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote dashboard data for {len(bugs)} bugs to {output_path}")
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
