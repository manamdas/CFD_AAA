#!/usr/bin/env python3
"""Save dumpcr details for each bug listed in a JSON file."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


BUG_ID_PATTERN = re.compile(r"CSC[A-Za-z0-9]+\Z")


def read_bug_ids(json_path: Path) -> list[str]:
    with json_path.open(encoding="utf-8") as json_file:
        records: Any = json.load(json_file)

    if not isinstance(records, list):
        raise ValueError(f"expected a JSON array in {json_path}")

    bug_ids: list[str] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"entry {index} in {json_path} is not a JSON object")
        bug_id = record.get("bugid") or record.get("Identifier")
        if not isinstance(bug_id, str) or not BUG_ID_PATTERN.fullmatch(bug_id):
            raise ValueError(f"entry {index} has an invalid or missing bugid: {bug_id!r}")
        if bug_id not in seen:
            bug_ids.append(bug_id)
            seen.add(bug_id)
    return bug_ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run dumpcr in csh and save details for each bug from bugs.json."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="bugs.json",
        help="JSON array containing bugid or Identifier fields (default: bugs.json)",
    )
    parser.add_argument(
        "--output-dir",
        default="bug_info",
        help="directory for dumpcr text files (default: bug_info)",
    )
    parser.add_argument(
        "--csh",
        default="csh",
        help="csh executable to use (default: csh)",
    )
    args = parser.parse_args()

    try:
        csh = shutil.which(args.csh)
        if csh is None:
            raise FileNotFoundError(f"cannot find csh executable: {args.csh}")
        bug_ids = read_bug_ids(Path(args.input))
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        failures = 0
        for bug_id in bug_ids:
            output_path = output_dir / f"bug_{bug_id}.txt"
            # IDs are validated above before being inserted into the csh command.
            command = f"dumpcr {bug_id}"
            with output_path.open("w", encoding="utf-8", errors="replace") as output:
                result = subprocess.run(
                    [csh, "-c", command],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
            if result.returncode:
                failures += 1
                print(
                    f"Failed ({result.returncode}): {bug_id}; see {output_path}",
                    file=sys.stderr,
                )
            else:
                print(f"Saved {output_path}", file=sys.stderr)

        print(
            f"Processed {len(bug_ids)} bugs; {failures} dumpcr command(s) failed.",
            file=sys.stderr,
        )
        return 1 if failures else 0
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
