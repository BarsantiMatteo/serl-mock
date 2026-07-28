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

---

## Phase 0 — Safety net before refactoring

- [ ] **Add baseline regression tests for current edition08 output.**
  Entails: create a `tests/` directory (none exists today); run the pipeline with a small
  `n_households` and fixed `seed`; assert output columns, dtypes, and row counts for each
  dataset (EPC, survey, covid survey, follow-up survey, participant summary, HH smart meter,
  daily smart meter, rt-summary, climate) against a committed snapshot. This is what lets us
  refactor freely afterwards without silently breaking edition08.

## Phase 1 — Output format abstraction

- [ ] **Add a format-aware writer.** Replace `write_csv()` in `utils.py` with something like
  `write_table(df, path_stem, format="csv")` that dispatches to `.to_csv()` or `.to_parquet()`
  and appends the correct extension itself (callers currently build the full `.csv` path
  manually).
  Entails: adding `pyarrow` (or `fastparquet`) as a dependency in `pyproject.toml`.
- [ ] **Stop hardcoding `.csv` in `with_edition_suffix()`.** It currently does
  `f"{basename}_edition{edition}.csv"` unconditionally — needs to accept/derive the extension
  instead.
- [ ] **Update all call sites** to use the new writer + suffix function:
  `generator_smartmeter.py` (HH monthly, daily yearly, rt-summary writes), `generator_contextual_data.py`
  (EPC, survey, covid survey, summary, follow-up), `generator_household_traits.py`, and
  `weather_downloader.py` (which currently bypasses `write_csv` entirely and calls `.to_csv`
  directly — fold it into the same abstraction while touching this).
- [ ] **Add a `format` setting to config**, defaulting to `"csv"` so edition08 output is
  byte-for-byte unchanged until Edition09 explicitly opts into `"parquet"`.

## Phase 2 — Edition as a first-class object, not a string

- [ ] **Design the edition config structure.** Decide between (a) one file per edition —
  `config/editions/edition08.yaml`, `config/editions/edition09.yaml` — with inheritance from a
  shared base, or (b) a single `serl_mock.yaml` with an `editions:` map keyed by edition number.
  Either way, each edition entry should declare: edition number, output format, and any
  edition-specific parameters (e.g. EPC age-band prefix rule) or schema-version references.
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

- [ ] **Define the Edition09 config**: `format: parquet` plus any schema overrides once the
  real Edition09 spec is available.
- [ ] **Verify both editions generate correctly from config alone** — edition08 → CSV,
  edition09 → Parquet — with no code branching on edition number outside the schema/format
  definitions themselves.
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
