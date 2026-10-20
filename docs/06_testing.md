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

### 1b. Timestamp conventions — `test_hh_timestamps.py`

Pins how HH smart-meter readings are timed (SERL stamps each reading at the **end** of the
half-hour it measures):
- unit tests for `HH` and `Read_date_effective_local` on normal days and both clock-change
  days (46 / 50 half-hours), including the midnight read after the 23-hour spring day;
- on the generated 2021 fixture: every monthly file runs from 00:30 on the 1st to 00:00 on
  the 1st of the next month, effective dates cover exactly 2021, and `HH` never repeats and
  covers 1–46/48/50 within each effective day.

A failure here means the half-hour ↔ day ↔ `HH` mapping drifted — downstream pipelines
assign readings to days and clock times from these columns.

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

Each edition has its own golden manifest, compared independently — `tests/golden/edition08_manifest.json`
and `tests/golden/edition09_manifest.json` today. Edition09 having different columns or a
different file layout than edition08 is expected and will not fail edition08's test. Adding a
new edition to `tests/_shared.py::TINY_CONFIGS_BY_EDITION` and running
`scripts/update_golden_manifest.py --edition <N>` gives it the same coverage.

### 4. MasterSERL harmonised survey / raw 2025 survey — `test_harmonised_survey.py`, `test_2025_survey.py`

Both mix direct unit tests (calling `build_harmonised_survey_dataframe()` / `build_2025_survey_dataframe()`
from `generator_contextual_data.py` directly, with a tiny synthetic mapping or the real
`serl_master_mapping_edition09.csv`, no full pipeline run) and pipeline-level tests (via `run_all()`,
checking `generate.harmonised_survey` / `generate.survey_2025` are off by default and produce the
expected file when turned on). Coverage includes: the `999999` "not asked" sentinel, the raw
2025 survey's documented skip logic actually blanking dependent columns, participation coverage
(not every PUPRN gets every survey occurrence), and determinism (same seed → identical output).
A failure here usually means either the coding templates in `generator_contextual_data.py`
changed shape, or the skip-logic/presence rules stopped matching the master mapping.

### 5. Synthetic missingness — `test_missingness.py`

Runs the smart-meter steps on `TINY_CONFIG` plus a `missingness:` block
covering every type and effect (edition08, and once for edition09), and
checks the HH files, daily files and `rt_summary` against the written
ground truth: dropped rows absent, flags and emptied values where planted,
trait selection and time windows respected, duplicates and off-grid
counts, incomplete daily sums, `missing` counting absent and flag-`0`
reads. It also compares with the session's plain fixture run: every
reading missingness didn't touch must be identical (its rngs are separate
from the generator's). Unit tests cover determinism and config
validation. The golden manifests don't include missingness, so they're
unaffected.

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
