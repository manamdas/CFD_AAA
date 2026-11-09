#!/usr/bin/env python3
"""Run the AAA bug query and format its results with qreport.pl."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path


QUERY = (
    "Project:CSC.datacenter and "
    "Product:n9k-standalone-sw,n9k-hw,fretta-nxos-sw,all and "
    "Found-during:Customer and "
    "Component:aaa,tacacs,radius,ldap and "
    "Status:R,V,M[200101:]"
)
REPORT_FIELDS = [
    "Identifier",
    "Headline",
    "Component",
    "Status",
    "Attribute",
    "Automated-test",
    "Verified-confidence",
]


def normalize_header(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def canonical_header(value: str) -> str:
    normalized = normalize_header(value)
    # qreport labels the Identifier column as "Id" in its HTML output.
    return "identifier" if normalized == "id" else normalized


class ReportTableParser(HTMLParser):
    """Extract rows from the HTML table produced by qreport.pl -web."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell_parts: list[str] | None = None
        self.in_row = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
            self.in_row = True
        elif self.in_row and tag in {"td", "th"}:
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.cell_parts is not None:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell_parts is not None:
            self.row.append(" ".join("".join(self.cell_parts).split()))
            self.cell_parts = None
        elif tag == "tr" and self.in_row:
            if self.row:
                self.rows.append(self.row)
            self.row = []
            self.in_row = False


def report_to_bugs(report_html: str) -> list[dict[str, str]]:
    parser = ReportTableParser()
    parser.feed(report_html)

    normalized_fields = {normalize_header(field): field for field in REPORT_FIELDS}
    indexes: dict[str, int] | None = None
    data_rows: list[list[str]] = []
    for row in parser.rows:
        normalized_row = [canonical_header(cell) for cell in row]
        if all(field in normalized_row for field in normalized_fields):
            indexes = {
                field: normalized_row.index(canonical_header(field))
                for field in REPORT_FIELDS
            }
            continue
        if indexes is not None:
            data_rows.append(row)

    if indexes is None:
        raise ValueError("qreport output did not contain the expected HTML table headers")

    bugs: list[dict[str, str]] = []
    for row in data_rows:
        if not any(cell.strip() for cell in row):
            continue
        bug = {
            field: row[indexes[field]].strip() if indexes[field] < len(row) else ""
            for field in REPORT_FIELDS
        }
        bugid = bug["Identifier"]
        if bugid:
            bugs.append({"bugid": bugid, **bug})
    return bugs


def find_executable(value: str, name: str) -> str:
    executable = shutil.which(value)
    if executable is None:
        raise FileNotFoundError(f"cannot find {name}: {value}")
    return executable


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect customer-found AAA, TACACS, RADIUS, and LDAP bugs."
    )
    parser.add_argument(
        "output",
        nargs="?",
        default="bugs.json",
        help="JSON file to write (default: bugs.json); use - for stdout",
    )
    args = parser.parse_args()

    try:
        query_pl = find_executable(
            os.environ.get("QUERY_PL", "/usr/cisco/bin/query.pl"), "query.pl"
        )
        qreport = find_executable(
            os.environ.get("QREPORT_PL", "qreport.pl"), "qreport.pl"
        )
        query_result = subprocess.run(
            [query_pl, QUERY],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )

        report_command = [qreport, "-web", *REPORT_FIELDS]
        report_result = subprocess.run(
            report_command,
            input=query_result.stdout,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        bugs = report_to_bugs(report_result.stdout)
        if args.output == "-":
            json.dump(bugs, sys.stdout, indent=2, ensure_ascii=False)
            print()
        else:
            output_path = Path(args.output)
            with output_path.open("w", encoding="utf-8") as json_file:
                json.dump(bugs, json_file, indent=2, ensure_ascii=False)
                json_file.write("\n")
            print(f"AAA bug JSON saved to {output_path}", file=sys.stderr)
    except FileNotFoundError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 127
    except subprocess.CalledProcessError as error:
        print(
            f"Error: command failed with exit status {error.returncode}",
            file=sys.stderr,
        )
        return error.returncode or 1
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
