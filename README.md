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

## Build dashboard JSON

After the dump files exist, extract each bug ID and its current
`Integrated-releases` into `dashboard.json`:

```bash
python3 build_dashboard.py
```

The output has a `total_bugs` count and a `bugs` array. Each item contains a
`bugid` and an `integrated_releases` list. Deleted release entries in the
dumpcr history are excluded. Use `--input-dir` or `--output` to choose different
paths.

Testcase specs in `testcases/<BUGID>.json` are embedded into that bug's
dashboard record when `build_dashboard.py` runs. The CSCvp62539 spec is a
proposed pyATS testcase based on its dumpcr reproduction and appears as an
expandable testcase entry in the dashboard.

## Run the web dashboard

Start the server from this project directory:

```bash
python3 dashboard_server.py
```

It serves the dashboard on port `3030` and reads `dashboard.json` from this
directory. Open `http://ads-784:3030` from a browser that can reach the ADS
host. The page shows bug counts and integrated releases, supports filtering,
and refreshes the data once per minute.
