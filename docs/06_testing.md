# Testing

This describes the automated test suite under `tests/`. The suite is a **structural safety
net**, not a data-quality check — since `serl_mock` generates mock data, there is no
"correct" value to assert on. What the tests protect is the *shape* of the output (files,
columns, dtypes, row counts) staying stable unless someone deliberately changes it.

## Running the tests

```bash
uv run pytest                          # everything
uv run pytest tests/test_household_traits.py -v   # one file, verbose
uv run pytest -k golden                # anything matching "golden"
```

No CDS API credentials or network access are required — weather download is always skipped in
tests (see [Fixture config](#fixture-config) below).

## Reading the output

Standard pytest output: a `.` (or a test name with `PASSED`, in `-v` mode) per passing test, `F`
(`FAILED`) per failing one, followed by a traceback for each failure. A clean run looks like:

```
tests/test_golden_manifest.py::test_edition08_output_matches_golden_manifest PASSED
tests/test_household_traits.py::test_columns_and_dtypes PASSED
...
7 passed in 2.1s
```

`0 failed` / all green means: the pipeline ran end-to-end against the fixture config, produced
every expected file, and every dataset's shape matches what was committed as correct. Anything
else means a change (intentional or not) altered structure — read the specific failure below to
find out which.

## Types of test

### 1. Unit tests — `test_household_traits.py`

Call generator functions directly (e.g. `generate_household_traits()`) with an in-memory PUPRN
list — no config file, no disk I/O, no full pipeline run. Fast, and the first place to look if
household-trait logic changes. A failure here means the function's columns, trait-fraction
counts, or reproducibility (same seed → same output) changed.

### 2. Pipeline smoke test — `test_pipeline_smoke.py`

Runs the *whole* pipeline once (via the session-scoped `generated_output_dir` fixture — every
test in a session shares one run, so this cost is paid once) and checks:
- every expected output file exists (EPC, survey, smart-meter monthly/yearly files, etc.)
- PUPRNs are identical across `puprn_master.csv`, `household_traits.csv`, EPC, and survey output

A failure here means either a file didn't get created (something crashed or a filename changed)
or a dataset lost the shared-PUPRN guarantee documented in
[00_overview.md](00_overview.md#consistency-caveat-for-mock-outputs).

### 3. Golden manifest regression test — `test_golden_manifest.py`

The main tripwire. It builds a **manifest** — for every CSV or Parquet file under the generated
output, its relative path, column names, dtypes, and row count (never cell values) — and
compares it against the committed baseline at `tests/golden/edition08_manifest.json`.
`build_manifest()` (in `tests/_shared.py`) is format-agnostic by design: it discovers both
`*.csv` and `*.parquet` files so it stays correct once an edition switches format instead of
silently seeing zero files for a Parquet edition. `tests/test_manifest_helper.py` proves this
directly against mock CSV/Parquet fixtures, since the real pipeline doesn't produce Parquet
yet (that's Phase 1 of the edition plan).

A failure prints exactly what changed, one of three ways:
- `files present in golden manifest but not generated: [...]` — a file that used to be produced
  is now missing.
- `files generated but not in golden manifest: [...]` — a new/renamed file appeared.
- `output shape changed for: {...}` — an existing file's columns, dtypes, or row count changed;
  the printed dict shows `expected` vs `actual` side by side.

**If the change is a bug**, fix the code and re-run — the test should pass again.

**If the change is intentional** (you meant to add/rename a column, change a dtype, etc. for
edition08 specifically), update the baseline deliberately:

```bash
uv run python scripts/update_golden_manifest.py
```

Then review the diff in `tests/golden/edition08_manifest.json` before committing it — this file
existing means someone has consciously confirmed the new shape is correct, not that it
regenerates itself silently. Never edit the JSON by hand.

Note this manifest is scoped to **edition08 only**. When edition09 exists it will get its own
`tests/golden/edition09_manifest.json`, compared independently — edition09 having different
columns or a different file layout is expected and will not fail edition08's test.

## Fixture config

All tests run against one small, deterministic config (`tests/_shared.py::TINY_CONFIG`), not the
real `config/serl_mock.yaml`:

| Setting | Value | Why |
|---|---|---|
| `n_households` | 5 | Large enough for every trait fraction to round to a non-zero count, small enough to run in ~2 seconds |
| `start_year` / `end_year` | 2021 | One year of half-hourly data — real coverage without a slow test |
| `seed` | 123 | Fixed, so output is byte-identical across runs |

Everything runs under a pytest `tmp_path`, so tests never read or write the real `config/` or
`data/mock/` directories.

## What isn't covered yet

- Value-domain checks (e.g. EPC energy ratings, survey answer categories only take known values).
- A "logical dataset" test helper that reassembles multi-file datasets before asserting — needed
  once an edition can split smart-meter data differently (no edition does yet).
