# AAA bug report collector

`collect_aaa_bugs.py` runs the Cisco bug query for customer-found bugs in the
`aaa`, `tacacs`, `radius`, and `ldap` components, then returns a JSON array.
Each object has a `bugid` field (a copy of `Identifier`) and the selected
qreport fields.

```bash
./collect_aaa_bugs.py
# or: python3 collect_aaa_bugs.py
```

By default, JSON is saved to `bugs.json` in the current directory. Pass `-` to
write JSON to standard output, or provide another output filename:

```bash
./collect_aaa_bugs.py
./collect_aaa_bugs.py /tmp/aaa-bugs.json
./collect_aaa_bugs.py -
```

The query tool defaults to `/usr/cisco/bin/query.pl`; the report tool defaults
to `qreport.pl` on `PATH`. Override either path with `QUERY_PL` or
`QREPORT_PL` if needed. Both tools must be installed and usable in the Cisco
environment where the script runs.

## Save full bug details

After creating `bugs.json`, run `dump_bug_info.py` to execute `dumpcr` in `csh`
for each bug ID. The output for each bug is saved as
`bug_info/bug_<bugid>.txt`:

```bash
python3 dump_bug_info.py
```

You can provide another JSON file, output directory, or `csh` executable:

```bash
python3 dump_bug_info.py other-bugs.json --output-dir bug_info
python3 dump_bug_info.py bugs.json --csh /bin/csh
```

## Generate testcase drafts from selected dumpcr files

`draft_testcases_from_dumpcr.py` reads selected dumpcr files and writes a
behavior-focused JSON testcase spec to `testcases/<BUGID>.json`. It separates
the report symptom and conditions from the bug-specific action, expected fixed
behavior, failure condition, evidence, and cleanup. It keeps dumpcr facts and
inferred behavior separate and cites note line ranges. Numbered dump steps and
transcript commands remain contextual evidence; they are not automatically
treated as independent coverage criteria. A `V` status records historical bug
verification; it does not by itself define the assertion or prove that the
generated testcase ran. The script never executes commands found in a dumpcr.

Generate selected behavior-focused specs (existing files are preserved unless
`--force` is supplied):

```bash
.venv/bin/python3 draft_testcases_from_dumpcr.py CSCvq73077 CSCvq95571
```

For another selected set, pass the bug IDs as arguments. If a draft already
exists, the script leaves it unchanged unless `--force` is supplied. Use
`--input-dir` and `--output-dir` to select different directories.

## Build dashboard JSON

After the dump files exist, extract each bug ID and its current
`Integrated-releases` into `dashboard.json`. The builder also merges
`coverage_results.json` into the dashboard. The builder recalculates counts
from per-bug assessments instead of trusting stale summary numbers. The current
score scope contains CSCvt64662, CSCvp62539, CSCvz55570, CSCwa16832, and
CSCwe72648.

Run the complete local flow with:

```bash
.venv/bin/python3 run_dumpcr_pipeline.py
```

The pipeline creates a draft for an in-scope bug if one is missing, validates
dumpcr references and TaskLog testcase/result ranges, applies the Codex-reviewed
behavior-to-log assessment, recalculates draft coverage, and rebuilds the
dashboard. It does not infer semantic coverage from keywords or run the drafted
testcase on a device. Existing source-reviewed specs are preserved; use
`--force-drafts` only when you intend to replace them with freshly extracted
drafts.

```bash
.venv/bin/python build_dashboard.py
```

The output has a `total_bugs` count, a `bugs` array, and a `coverage` summary.
Each bug includes a `bugid` and an `integrated_releases` list; the five in-scope
bugs also include a `coverage` record with log evidence and the candidate test
execution result.
Deleted release entries in the dumpcr history are excluded. Use `--input-dir`,
`--output`, or `--coverage` to choose different paths. The draft assessment
uses reviewed testcase criteria and surrounding log evidence; a passing
related test alone does not prove bug coverage. `NOT_EXERCISED` means the log
lacks the required behavior; `UNVERIFIED` means the evidence mapping itself
could not be checked. Both remain separate from runtime execution status.

Testcase specs in `testcases/<BUGID>.json` are embedded into that bug's
dashboard record when `build_dashboard.py` runs. The five current specs are
source-reviewed behavior plans with expandable source facts, interpretations,
uncertainties, and requirements. The supplied workspace contains
`TaskLog.Task-1` but not the suite source/class assertion files. The pipeline
therefore produces a Codex-reviewed **draft** assessment from the log; it does
not claim the generated testcase itself was executed.

## Run the web dashboard

Start the server from this project directory:

```bash
python3 dashboard_server.py
```

It serves the dashboard on port `3030` and reads `dashboard.json` from this
directory. Open `http://ads-784:3030` from a browser that can reach the ADS
host. The page separates testcase status, archived suite execution, log
exercise status, coverage result, evidence, and integrated releases. It supports
filtering and refreshes the data once per minute.
