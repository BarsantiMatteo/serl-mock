# Working plan: multi-edition & multi-format support

This is the tracked plan for making `serl-mock` support **multiple SERL editions side by
side**, where each edition may change both the **data content** (schema, value rules) and
the **output format** (e.g. Edition09 using Parquet instead of CSV).

Related: [06_working_notes.md](06_working_notes.md) for unrelated feature TODOs (tariff data,
error injection, etc.) — those are orthogonal to this plan and can land independently.

## Why this is needed

Today "edition" is only a string used to build filenames (`with_edition_suffix()` in
[utils.py](../src/serl_mock/utils.py)). Output format (`.csv`) is hardcoded in three places
(`with_edition_suffix`, `write_csv`, and each generator's own filename method), and dataset
schemas (EPC fields, smart-meter columns, survey value rules) are hardcoded in the generator
classes rather than versioned. As a result, past edition-to-edition changes (e.g. the EPC
age-band format, nation-specific vocab) were implemented by editing the shared code in place —
which means the previous edition's behaviour is no longer reproducible from this codebase.
This plan fixes that before Edition09 (Parquet) work starts, so both editions can coexist.

## Guiding principle

Keep this incremental — no big-bang rewrite. Each phase should leave the tool in a working,
edition08-compatible state. Only build schema/format machinery for the pieces that actually
need to vary; don't pre-version datasets that haven't changed yet.

## Three independent axes of variation

An edition can change along three separate axes, and they should be free to vary
independently rather than being conflated into one "edition" switch:

| Axis | Example | Owned by (target state) |
|---|---|---|
| **Format** | CSV vs Parquet | `format` setting per edition (Phase 1) |
| **Layout** | one CSV per calendar month vs one combined file for the whole period | a per-dataset `layout` setting per edition (Phase 1/2) |
| **Schema** | column names/types, value vocab, regional rules | versioned schema definitions per dataset per edition (Phase 3) |

Layout matters because generators today hardcode the physical split, not just the format —
e.g. `HHSmartMeterGenerator.generate_all()` loops over months and calls `write_month` once per
month (`generator_smartmeter.py:313-319`). "One combined file instead of one per month" is
neither a format change nor a schema change; it needs its own pluggable strategy (see Phase 1).

---

## Phase 0 — Safety net before refactoring

The goal is a tripwire that fires on *any* unintended structural change during Phases 1-3,
without assuming a fixed shape that later editions are free to legitimately change. Concretely:

- [ ] **Set up `tests/` with pytest** (add as a dev dependency in `pyproject.toml`) and a
  `conftest.py` fixture that builds a tiny deterministic config (`n_households=5`,
  `start_year=end_year=2021`, fixed `seed`) plus a temp PUPRN list, all under `tmp_path` so
  nothing touches real `config/`/`data/`.
- [ ] **Give `run_all()` an injectable config/output path.** It currently hardcodes
  `CONFIG_DIR / "serl_mock.yaml"` (`scripts/generate_mock_data.py:53`), so there's no way to
  run the full pipeline against a temp config today. This is a prerequisite for any end-to-end
  test, independent of the edition work.
- [ ] **Per-dataset unit tests** (`tests/test_household_traits.py`, `test_smartmeter.py`,
  `test_contextual_data.py`): columns/dtypes match a committed baseline list, value-domain
  checks for categorical fields (EPC rating, nation, quality flags), and the cross-dataset
  consistency guarantees already claimed in `00_overview.md` (trait alignment, PUPRN
  consistency, nation↔region agreement) checked automatically instead of by eye.
- [ ] **Golden manifests, scoped per edition — not one global snapshot.** Commit
  `tests/golden/edition08_manifest.json` capturing today's actual output (file paths, columns,
  dtypes, row counts) for the tiny fixture config. When Edition09 lands with different columns
  or a different layout, it gets its *own* `edition09_manifest.json` — it is never diffed
  against edition08's. A manifest only fires when *that specific* edition's own committed
  behaviour drifts, which keeps this safety net compatible with editions legitimately changing
  shape.
- [ ] **Write structural tests against the logical dataset, not the physical files.** Shared
  test helpers should read *whatever files match a given edition's pattern* for a dataset,
  concatenate them, and assert on the reassembled table (one row per `PUPRN`/timestamp, no
  duplicates, row count = households × time-steps, columns match that edition's schema). That
  way the same test works whether the edition splits smart-meter data into 12 monthly files or
  one combined file — only a small, edition-specific check asserts the physical file count/name
  pattern itself.
- [ ] **Add a CI workflow that runs the full test suite (all editions' manifests) on every
  PR.** There is no CI in this repo today (no `.github/` directory), so nothing currently stops
  a change made for a later edition from silently regressing an earlier one — it relies on
  someone remembering to run tests locally. A required GitHub Actions check turns that into an
  enforced gate: any PR that changes a committed edition's manifest fails the build until a
  human explicitly updates that edition's golden file, which is the conscious "yes, this
  should change edition08 too" decision called out above, not an accident.

## Phase 1 — Output format & layout abstraction

- [ ] **Add a format-aware writer.** Replace `write_csv()` in `utils.py` with something like
  `write_table(df, path_stem, format="csv")` that dispatches to `.to_csv()` or `.to_parquet()`
  and appends the correct extension itself (callers currently build the full `.csv` path
  manually).
  Entails: adding `pyarrow` (or `fastparquet`) as a dependency in `pyproject.toml`.
- [ ] **Stop hardcoding `.csv` in `with_edition_suffix()`.** It currently does
  `f"{basename}_edition{edition}.csv"` unconditionally — needs to accept/derive the extension
  instead.
- [ ] **Extract a pluggable layout strategy** for datasets whose physical split is currently
  hardcoded in the generator (`HHSmartMeterGenerator`/`DailySmartMeterGenerator` in
  `generator_smartmeter.py`). Turn "loop over months, write one file per month" into
  `layout.partition(df) -> [(filename_stem, chunk_df), ...]`, with a `monthly` strategy that
  reproduces current behaviour exactly and a `single_file` strategy that returns one chunk.
  `generate_all()` then just calls `write_table` per chunk, regardless of layout.
- [ ] **Update all call sites** to use the new writer + suffix function:
  `generator_smartmeter.py` (HH monthly, daily yearly, rt-summary writes), `generator_contextual_data.py`
  (EPC, survey, covid survey, summary, follow-up), `generator_household_traits.py`, and
  `weather_downloader.py` (which currently bypasses `write_csv` entirely and calls `.to_csv`
  directly — fold it into the same abstraction while touching this).
- [ ] **Add `format` and per-dataset `layout` settings to config**, both defaulting to today's
  behaviour (`csv` / `monthly`) so edition08 output is byte-for-byte unchanged until Edition09
  explicitly opts into `parquet` / `single_file`.

## Phase 2 — Edition as a first-class object, not a string

- [ ] **Design the edition config structure.** Decide between (a) one file per edition —
  `config/editions/edition08.yaml`, `config/editions/edition09.yaml` — with inheritance from a
  shared base, or (b) a single `serl_mock.yaml` with an `editions:` map keyed by edition number.
  Either way, each edition entry should declare: edition number, output format, per-dataset
  layout (e.g. `hh_smart_meter: {layout: monthly}` for edition08 vs `{layout: single_file}` for
  edition09), and any edition-specific parameters (e.g. EPC age-band prefix rule) or
  schema-version references.
- [ ] **Implement an `Edition` loader/object** (e.g. `src/serl_mock/edition.py`) that resolves
  the active edition's config once at startup and exposes it as a single object.
- [ ] **Thread the `Edition` object through the generators**, replacing the ~5 separate
  `cfg.get("edition", "08")` reads currently duplicated in `generator_smartmeter.py`,
  `generator_contextual_data.py`, `weather_downloader.py`, and `generator_household_traits.py`.
- [ ] **Derive output directory names from the active edition** in `paths.py` instead of the
  current hardcoded `edition08` literal in `MOCK_HH_DIR`, `MOCK_DAILY_DIR`, `MOCK_CLIMATE_DIR`.

## Phase 3 — Versioned schema/dictionaries per dataset

- [ ] **Audit each dataset for what's actually edition-invariant vs edition-specific**
  (EPC, survey, covid survey, follow-up survey, smart-meter, climate). Only the fields/rules
  that actually differ between editions need versioning — this avoids over-building.
- [ ] **Extract the EPC field list + value pools** (`_epc_fields()` and `generate_epc()` in
  `generator_contextual_data.py`) into a versioned schema definition, starting with an
  edition08 baseline that reproduces current behaviour exactly.
- [ ] **Extract smart-meter column definitions** (`generator_smartmeter.py`) into a versioned
  form — likely lower priority since these haven't varied by edition historically; do this only
  once Edition09's actual smart-meter spec is known to require it.
- [ ] **Version the survey dictionaries properly.** Reference dictionaries in `data/reference/`
  are still labelled `edition07` while output is `edition08` — add real per-edition dictionary
  files (or a documented mapping) instead of the current implicit drift.

## Phase 4 — Bring up Edition09 as the first real test of the new system

- [ ] **Define the Edition09 config**: `format: parquet`, any layout changes (e.g.
  `single_file` for smart-meter data if that's the real spec), plus any schema overrides once
  the real Edition09 spec is available.
- [ ] **Verify both editions generate correctly from config alone** — edition08 → CSV, monthly
  files; edition09 → Parquet, its own layout — with no code branching on edition number outside
  the schema/format/layout definitions themselves.
- [ ] **Update docs** (`00_overview.md`, `02_configuration.md`, `01_structure.md`) to describe
  the new edition/format system, replacing the current "edition = filename suffix" description
  in `00_overview.md`.

## Phase 5 — Cleanup

- [ ] **Remove now-dead old plumbing**: the old string-only `with_edition_suffix` behaviour,
  scattered `.zfill(2)` edition-string handling in `scripts/generate_mock_data.py`.
- [ ] **Extend the Phase 0 regression tests to cover both editions**, so future edition
  additions can't silently regress an existing one.

---

## Status

Not started. Update the checkboxes above as work lands; add a short note under each phase if
the approach changes from what's described here.
